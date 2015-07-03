import py
from rpython.jit.metainterp.history import (Box, Const, ConstInt, ConstPtr,
    ConstFloat, BoxInt, BoxFloat, BoxVector, BoxVectorAccum, INT, REF,
    FLOAT, VECTOR, TargetToken)
from rpython.jit.backend.llsupport.descr import (ArrayDescr, CallDescr,
    unpack_arraydescr, unpack_fielddescr, unpack_interiorfielddescr)
from rpython.jit.backend.x86.regloc import (FrameLoc, RegLoc, ConstFloatLoc,
    FloatImmedLoc, ImmedLoc, imm, imm0, imm1, ecx, eax, edx, ebx, esi, edi,
    ebp, r8, r9, r10, r11, r12, r13, r14, r15, xmm0, xmm1, xmm2, xmm3, xmm4,
    xmm5, xmm6, xmm7, xmm8, xmm9, xmm10, xmm11, xmm12, xmm13, xmm14,
    X86_64_SCRATCH_REG, X86_64_XMM_SCRATCH_REG, AddressLoc)

def addr_add(reg_or_imm1, reg_or_imm2, offset=0, scale=0):
    # duplicated for easy migration, def in assembler.py as well
    return AddressLoc(reg_or_imm1, reg_or_imm2, scale, offset)

class VectorAssemblerMixin(object):
    _mixin_ = True

    def _guard_vector_true(self, guard_op, loc, zero=False):
        arg = guard_op.getarg(0)
        assert isinstance(arg, BoxVector)
        size = arg.item_size
        temp = X86_64_XMM_SCRATCH_REG
        #
        self.mc.PXOR(temp, temp)
        # if the vector is not fully packed blend 1s
        if not arg.fully_packed(self.cpu.vector_register_size):
            self.mc.PCMPEQQ(temp, temp) # fill with ones
            select = 0
            bits_used = (arg.item_count * arg.item_size * 8)
            index = bits_used // 16
            while index < 8:
                select |= (1 << index)
                index += 1
            self.mc.PBLENDW_xxi(loc.value, temp.value, select)
            # reset to zeros
            self.mc.PXOR(temp, temp)

        self.mc.PCMPEQ(size, loc, temp)
        self.mc.PCMPEQQ(temp, temp)
        self.mc.PTEST(loc, temp)

    # vector operations
    # ________________________________________

    def _accum_update_at_exit(self, fail_locs, fail_args, faildescr, regalloc):
        """ If accumulation is done in this loop, at the guard exit
        some vector registers must be adjusted to yield the correct value"""
        assert regalloc is not None
        accum_info = faildescr.rd_accum_list
        while accum_info:
            pos = accum_info.position
            loc = fail_locs[pos]
            assert isinstance(loc, RegLoc)
            arg = fail_args[pos]
            if isinstance(arg, BoxVectorAccum):
                arg = arg.scalar_var
            assert arg is not None
            tgtloc = regalloc.force_allocate_reg(arg, fail_args)
            if accum_info.operation == '+':
                # reduction using plus
                self._accum_reduce_sum(arg, loc, tgtloc)
            elif accum_info.operation == '*':
                self._accum_reduce_mul(arg, loc, tgtloc)
            else:
                not_implemented("accum operator %s not implemented" %
                                            (accum_info.operation)) 
            fail_locs[pos] = tgtloc
            regalloc.possibly_free_var(arg)
            accum_info = accum_info.prev

    def _accum_reduce_mul(self, arg, accumloc, targetloc):
        scratchloc = X86_64_XMM_SCRATCH_REG
        self.mov(accumloc, scratchloc)
        # swap the two elements
        self.mc.SHUFPD_xxi(scratchloc.value, scratchloc.value, 0x01)
        self.mc.MULSD(accumloc, scratchloc)
        if accumloc is not targetloc:
            self.mov(accumloc, targetloc)

    def _accum_reduce_sum(self, arg, accumloc, targetloc):
        # Currently the accumulator can ONLY be the biggest
        # size for X86 -> 64 bit float/int
        if arg.type == FLOAT:
            # r = (r[0]+r[1],r[0]+r[1])
            self.mc.HADDPD(accumloc, accumloc)
            # upper bits (> 64) are dirty (but does not matter)
            if accumloc is not targetloc:
                self.mov(accumloc, targetloc)
            return
        elif arg.type == INT:
            scratchloc = X86_64_SCRATCH_REG
            self.mc.PEXTRQ_rxi(targetloc.value, accumloc.value, 0)
            self.mc.PEXTRQ_rxi(scratchloc.value, accumloc.value, 1)
            self.mc.ADD(targetloc, scratchloc)
            return

        not_implemented("reduce sum for %s not impl." % arg)

    def genop_vec_getarrayitem_raw(self, op, arglocs, resloc):
        # considers item scale (raw_load does not)
        base_loc, ofs_loc, size_loc, ofs, integer_loc, aligned_loc = arglocs
        scale = get_scale(size_loc.value)
        src_addr = addr_add(base_loc, ofs_loc, ofs.value, scale)
        self._vec_load(resloc, src_addr, integer_loc.value,
                       size_loc.value, aligned_loc.value)

    def genop_vec_raw_load(self, op, arglocs, resloc):
        base_loc, ofs_loc, size_loc, ofs, integer_loc, aligned_loc = arglocs
        src_addr = addr_add(base_loc, ofs_loc, ofs.value, 0)
        self._vec_load(resloc, src_addr, integer_loc.value,
                       size_loc.value, aligned_loc.value)

    def _vec_load(self, resloc, src_addr, integer, itemsize, aligned):
        if integer:
            if aligned:
                self.mc.MOVDQA(resloc, src_addr)
            else:
                self.mc.MOVDQU(resloc, src_addr)
        else:
            if itemsize == 4:
                self.mc.MOVUPS(resloc, src_addr)
            elif itemsize == 8:
                self.mc.MOVUPD(resloc, src_addr)

    def genop_discard_vec_setarrayitem_raw(self, op, arglocs):
        # considers item scale (raw_store does not)
        base_loc, ofs_loc, value_loc, size_loc, baseofs, integer_loc, aligned_loc = arglocs
        scale = get_scale(size_loc.value)
        dest_loc = addr_add(base_loc, ofs_loc, baseofs.value, scale)
        self._vec_store(dest_loc, value_loc, integer_loc.value,
                        size_loc.value, aligned_loc.value)

    def genop_discard_vec_raw_store(self, op, arglocs):
        base_loc, ofs_loc, value_loc, size_loc, baseofs, integer_loc, aligned_loc = arglocs
        dest_loc = addr_add(base_loc, ofs_loc, baseofs.value, 0)
        self._vec_store(dest_loc, value_loc, integer_loc.value,
                        size_loc.value, aligned_loc.value)

    def _vec_store(self, dest_loc, value_loc, integer, itemsize, aligned):
        if integer:
            if aligned:
                self.mc.MOVDQA(dest_loc, value_loc)
            else:
                self.mc.MOVDQU(dest_loc, value_loc)
        else:
            if itemsize == 4:
                self.mc.MOVUPS(dest_loc, value_loc)
            elif itemsize == 8:
                self.mc.MOVUPD(dest_loc, value_loc)

    def genop_vec_int_mul(self, op, arglocs, resloc):
        loc0, loc1, itemsize_loc = arglocs
        itemsize = itemsize_loc.value
        if itemsize == 2:
            self.mc.PMULLW(loc0, loc1)
        elif itemsize == 4:
            self.mc.PMULLD(loc0, loc1)
        else:
            # NOTE see http://stackoverflow.com/questions/8866973/can-long-integer-routines-benefit-from-sse/8867025#8867025
            # There is no 64x64 bit packed mul and I did not find one
            # for 8 bit either. It is questionable if it gives any benefit
            # for 8 bit.
            not_implemented("int8/64 mul")

    def genop_vec_int_add(self, op, arglocs, resloc):
        loc0, loc1, size_loc = arglocs
        size = size_loc.value
        if size == 1:
            self.mc.PADDB(loc0, loc1)
        elif size == 2:
            self.mc.PADDW(loc0, loc1)
        elif size == 4:
            self.mc.PADDD(loc0, loc1)
        elif size == 8:
            self.mc.PADDQ(loc0, loc1)

    def genop_vec_int_sub(self, op, arglocs, resloc):
        loc0, loc1, size_loc = arglocs
        size = size_loc.value
        if size == 1:
            self.mc.PSUBB(loc0, loc1)
        elif size == 2:
            self.mc.PSUBW(loc0, loc1)
        elif size == 4:
            self.mc.PSUBD(loc0, loc1)
        elif size == 8:
            self.mc.PSUBQ(loc0, loc1)

    def genop_vec_int_and(self, op, arglocs, resloc):
        self.mc.PAND(resloc, arglocs[0])

    def genop_vec_int_or(self, op, arglocs, resloc):
        self.mc.POR(resloc, arglocs[0])

    def genop_vec_int_xor(self, op, arglocs, resloc):
        self.mc.PXOR(resloc, arglocs[0])

    genop_vec_float_arith = """
    def genop_vec_float_{type}(self, op, arglocs, resloc):
        loc0, loc1, itemsize_loc = arglocs
        itemsize = itemsize_loc.value
        if itemsize == 4:
            self.mc.{p_op_s}(loc0, loc1)
        elif itemsize == 8:
            self.mc.{p_op_d}(loc0, loc1)
    """
    for op in ['add','mul','sub']:
        OP = op.upper()
        _source = genop_vec_float_arith.format(type=op,
                                               p_op_s=OP+'PS',
                                               p_op_d=OP+'PD')
        exec py.code.Source(_source).compile()
    del genop_vec_float_arith

    def genop_vec_float_truediv(self, op, arglocs, resloc):
        loc0, loc1, sizeloc = arglocs
        size = sizeloc.value
        if size == 4:
            self.mc.DIVPS(loc0, loc1)
        elif size == 8:
            self.mc.DIVPD(loc0, loc1)

    def genop_vec_float_abs(self, op, arglocs, resloc):
        src, sizeloc = arglocs
        size = sizeloc.value
        if size == 4:
            self.mc.ANDPS(src, heap(self.single_float_const_abs_addr))
        elif size == 8:
            self.mc.ANDPD(src, heap(self.float_const_abs_addr))

    def genop_vec_float_neg(self, op, arglocs, resloc):
        src, sizeloc = arglocs
        size = sizeloc.value
        if size == 4:
            self.mc.XORPS(src, heap(self.single_float_const_neg_addr))
        elif size == 8:
            self.mc.XORPD(src, heap(self.float_const_neg_addr))

    def genop_vec_int_signext(self, op, arglocs, resloc):
        srcloc, sizeloc, tosizeloc = arglocs
        size = sizeloc.value
        tosize = tosizeloc.value
        if size == tosize:
            return # already the right size
        if size == 4 and tosize == 8:
            scratch = X86_64_SCRATCH_REG.value
            self.mc.PEXTRD_rxi(scratch, srcloc.value, 1)
            self.mc.PINSRQ_xri(resloc.value, scratch, 1)
            self.mc.PEXTRD_rxi(scratch, srcloc.value, 0)
            self.mc.PINSRQ_xri(resloc.value, scratch, 0)
        elif size == 8 and tosize == 4:
            # is there a better sequence to move them?
            scratch = X86_64_SCRATCH_REG.value
            self.mc.PEXTRQ_rxi(scratch, srcloc.value, 0)
            self.mc.PINSRD_xri(resloc.value, scratch, 0)
            self.mc.PEXTRQ_rxi(scratch, srcloc.value, 1)
            self.mc.PINSRD_xri(resloc.value, scratch, 1)
        else:
            # note that all other conversions are not implemented
            # on purpose. it needs many x86 op codes to implement
            # the missing combinations. even if they are implemented
            # the speedup might only be modest...
            # the optimization does not emit such code!
            msg = "vec int signext (%d->%d)" % (size, tosize)
            not_implemented(msg)

    def genop_vec_float_expand(self, op, arglocs, resloc):
        srcloc, sizeloc = arglocs
        size = sizeloc.value
        if isinstance(srcloc, ConstFloatLoc):
            # they are aligned!
            self.mc.MOVAPD(resloc, srcloc)
        elif size == 4:
            # the register allocator forces src to be the same as resloc
            # r = (s[0], s[0], r[0], r[0])
            # since resloc == srcloc: r = (r[0], r[0], r[0], r[0])
            self.mc.SHUFPS_xxi(resloc.value, srcloc.value, 0)
        elif size == 8:
            self.mc.MOVDDUP(resloc, srcloc)
        else:
            raise AssertionError("float of size %d not supported" % (size,))

    def genop_vec_int_expand(self, op, arglocs, resloc):
        srcloc, sizeloc = arglocs
        if not isinstance(srcloc, RegLoc):
            self.mov(srcloc, X86_64_SCRATCH_REG)
            srcloc = X86_64_SCRATCH_REG
        assert not srcloc.is_xmm
        size = sizeloc.value
        if size == 1:
            self.mc.PINSRB_xri(resloc.value, srcloc.value, 0)
            self.mc.PSHUFB(resloc, heap(self.expand_byte_mask_addr))
        elif size == 2:
            self.mc.PINSRW_xri(resloc.value, srcloc.value, 0)
            self.mc.PINSRW_xri(resloc.value, srcloc.value, 4)
            self.mc.PSHUFLW_xxi(resloc.value, resloc.value, 0)
            self.mc.PSHUFHW_xxi(resloc.value, resloc.value, 0)
        elif size == 4:
            self.mc.PINSRD_xri(resloc.value, srcloc.value, 0)
            self.mc.PSHUFD_xxi(resloc.value, resloc.value, 0)
        elif size == 8:
            self.mc.PINSRQ_xri(resloc.value, srcloc.value, 0)
            self.mc.PINSRQ_xri(resloc.value, srcloc.value, 1)
        else:
            raise AssertionError("cannot handle size %d (int expand)" % (size,))

    def genop_vec_int_pack(self, op, arglocs, resloc):
        resultloc, sourceloc, residxloc, srcidxloc, countloc, sizeloc = arglocs
        assert isinstance(resultloc, RegLoc)
        assert isinstance(sourceloc, RegLoc)
        size = sizeloc.value
        srcidx = srcidxloc.value
        residx = residxloc.value
        count = countloc.value
        # for small data type conversion this can be quite costy
        # NOTE there might be some combinations that can be handled
        # more efficiently! e.g.
        # v2 = pack(v0,v1,4,4)
        si = srcidx
        ri = residx
        k = count
        while k > 0:
            if size == 8:
                if resultloc.is_xmm and sourceloc.is_xmm: # both xmm
                    self.mc.PEXTRQ_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
                    self.mc.PINSRQ_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
                elif resultloc.is_xmm: # xmm <- reg
                    self.mc.PINSRQ_xri(resultloc.value, sourceloc.value, ri)
                else: # reg <- xmm
                    self.mc.PEXTRQ_rxi(resultloc.value, sourceloc.value, si)
            elif size == 4:
                if resultloc.is_xmm and sourceloc.is_xmm:
                    self.mc.PEXTRD_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
                    self.mc.PINSRD_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
                elif resultloc.is_xmm:
                    self.mc.PINSRD_xri(resultloc.value, sourceloc.value, ri)
                else:
                    self.mc.PEXTRD_rxi(resultloc.value, sourceloc.value, si)
            elif size == 2:
                if resultloc.is_xmm and sourceloc.is_xmm:
                    self.mc.PEXTRW_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
                    self.mc.PINSRW_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
                elif resultloc.is_xmm:
                    self.mc.PINSRW_xri(resultloc.value, sourceloc.value, ri)
                else:
                    self.mc.PEXTRW_rxi(resultloc.value, sourceloc.value, si)
            elif size == 1:
                if resultloc.is_xmm and sourceloc.is_xmm:
                    self.mc.PEXTRB_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
                    self.mc.PINSRB_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
                elif resultloc.is_xmm:
                    self.mc.PINSRB_xri(resultloc.value, sourceloc.value, ri)
                else:
                    self.mc.PEXTRB_rxi(resultloc.value, sourceloc.value, si)
            si += 1
            ri += 1
            k -= 1

    genop_vec_int_unpack = genop_vec_int_pack

    def genop_vec_float_pack(self, op, arglocs, resultloc):
        resloc, srcloc, residxloc, srcidxloc, countloc, sizeloc = arglocs
        assert isinstance(resloc, RegLoc)
        assert isinstance(srcloc, RegLoc)
        count = countloc.value
        residx = residxloc.value
        srcidx = srcidxloc.value
        size = sizeloc.value
        if size == 4:
            si = srcidx
            ri = residx
            k = count
            while k > 0:
                if resloc.is_xmm:
                    src = srcloc.value
                    if not srcloc.is_xmm:
                        # if source is a normal register (unpack)
                        assert count == 1
                        assert si == 0
                        self.mov(srcloc, X86_64_XMM_SCRATCH_REG)
                        src = X86_64_XMM_SCRATCH_REG.value
                    select = ((si & 0x3) << 6)|((ri & 0x3) << 4)
                    self.mc.INSERTPS_xxi(resloc.value, src, select)
                else:
                    self.mc.PEXTRD_rxi(resloc.value, srcloc.value, si)
                si += 1
                ri += 1
                k -= 1
        elif size == 8:
            assert resloc.is_xmm
            if srcloc.is_xmm:
                if srcidx == 0:
                    if residx == 0:
                        # r = (s[0], r[1])
                        self.mc.MOVSD(resloc, srcloc)
                    else:
                        assert residx == 1
                        # r = (r[0], s[0])
                        self.mc.UNPCKLPD(resloc, srcloc)
                else:
                    assert srcidx == 1
                    if residx == 0:
                        # r = (s[1], r[1])
                        if resloc != srcloc:
                            self.mc.UNPCKHPD(resloc, srcloc)
                        self.mc.SHUFPD_xxi(resloc.value, resloc.value, 1)
                    else:
                        assert residx == 1
                        # r = (r[0], s[1])
                        if resloc != srcloc:
                            self.mc.SHUFPD_xxi(resloc.value, resloc.value, 1)
                            self.mc.UNPCKHPD(resloc, srcloc)
                        # if they are equal nothing is to be done

    genop_vec_float_unpack = genop_vec_float_pack

    def genop_vec_cast_float_to_singlefloat(self, op, arglocs, resloc):
        self.mc.CVTPD2PS(resloc, arglocs[0])

    def genop_vec_cast_float_to_int(self, op, arglocs, resloc):
        self.mc.CVTPD2DQ(resloc, arglocs[0])

    def genop_vec_cast_int_to_float(self, op, arglocs, resloc):
        self.mc.CVTDQ2PD(resloc, arglocs[0])

    def genop_vec_cast_singlefloat_to_float(self, op, arglocs, resloc):
        self.mc.CVTPS2PD(resloc, arglocs[0])

class VectorRegallocMixin(object):
    _mixin_ = True

    def consider_vec_getarrayitem_raw(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr)
        assert not descr.is_array_of_pointers() and \
               not descr.is_array_of_structs()
        itemsize, ofs, _ = unpack_arraydescr(descr)
        integer = not (descr.is_array_of_floats() or descr.getconcrete_type() == FLOAT)
        aligned = False
        args = op.getarglist()
        base_loc = self.rm.make_sure_var_in_reg(op.getarg(0), args)
        ofs_loc = self.rm.make_sure_var_in_reg(op.getarg(1), args)
        result_loc = self.force_allocate_reg(op.result)
        self.perform(op, [base_loc, ofs_loc, imm(itemsize), imm(ofs),
                          imm(integer), imm(aligned)], result_loc)

    consider_vec_raw_load = consider_vec_getarrayitem_raw

    def consider_vec_setarrayitem_raw(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr)
        assert not descr.is_array_of_pointers() and \
               not descr.is_array_of_structs()
        itemsize, ofs, _ = unpack_arraydescr(descr)
        args = op.getarglist()
        base_loc = self.rm.make_sure_var_in_reg(op.getarg(0), args)
        value_loc = self.make_sure_var_in_reg(op.getarg(2), args)
        ofs_loc = self.rm.make_sure_var_in_reg(op.getarg(1), args)

        integer = not (descr.is_array_of_floats() or descr.getconcrete_type() == FLOAT)
        aligned = False
        self.perform_discard(op, [base_loc, ofs_loc, value_loc,
                                 imm(itemsize), imm(ofs), imm(integer), imm(aligned)])

    consider_vec_raw_store = consider_vec_setarrayitem_raw

    def consider_vec_arith(self, op):
        lhs = op.getarg(0)
        assert isinstance(lhs, BoxVector)
        size = lhs.item_size
        args = op.getarglist()
        loc1 = self.make_sure_var_in_reg(op.getarg(1), args)
        loc0 = self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
        self.perform(op, [loc0, loc1, imm(size)], loc0)

    consider_vec_int_add = consider_vec_arith
    consider_vec_int_sub = consider_vec_arith
    consider_vec_int_mul = consider_vec_arith
    consider_vec_float_add = consider_vec_arith
    consider_vec_float_sub = consider_vec_arith
    consider_vec_float_mul = consider_vec_arith
    consider_vec_float_truediv = consider_vec_arith
    del consider_vec_arith

    def consider_vec_arith_unary(self, op):
        lhs = op.getarg(0)
        assert isinstance(lhs, BoxVector)
        size = lhs.item_size
        args = op.getarglist()
        res = self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
        self.perform(op, [res, imm(size)], res)

    consider_vec_float_neg = consider_vec_arith_unary
    consider_vec_float_abs = consider_vec_arith_unary
    del consider_vec_arith_unary

    def consider_vec_logic(self, op):
        lhs = op.getarg(0)
        assert isinstance(lhs, BoxVector)
        size = lhs.item_size
        args = op.getarglist()
        source = self.make_sure_var_in_reg(op.getarg(1), args)
        result = self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
        self.perform(op, [source, imm(size)], result)

    consider_vec_float_eq = consider_vec_logic
    consider_vec_int_and = consider_vec_logic
    consider_vec_int_or = consider_vec_logic
    consider_vec_int_xor = consider_vec_logic
    del consider_vec_logic

    def consider_vec_int_pack(self, op):
        # new_res = vec_int_pack(res, src, index, count)
        arg = op.getarg(1)
        index = op.getarg(2)
        count = op.getarg(3)
        assert isinstance(index, ConstInt)
        assert isinstance(count, ConstInt)
        args = op.getarglist()
        srcloc = self.make_sure_var_in_reg(arg, args)
        resloc =  self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
        residx = index.value # where to put it in result?
        srcidx = 0
        assert isinstance(op.result, BoxVector)
        size = op.result.getsize()
        arglocs = [resloc, srcloc, imm(residx), imm(srcidx), imm(count.value), imm(size)]
        self.perform(op, arglocs, resloc)

    consider_vec_float_pack = consider_vec_int_pack

    def consider_vec_int_unpack(self, op):
        index = op.getarg(1)
        count = op.getarg(2)
        assert isinstance(index, ConstInt)
        assert isinstance(count, ConstInt)
        args = op.getarglist()
        srcloc = self.make_sure_var_in_reg(op.getarg(0), args)
        if isinstance(op.result, BoxVector):
            resloc =  self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
            assert isinstance(op.result, BoxVector)
            size = op.result.getsize()
        else:
            # unpack into iX box
            resloc =  self.force_allocate_reg(op.result, args)
            arg = op.getarg(0)
            assert isinstance(arg, BoxVector)
            size = arg.getsize()
        residx = 0
        args = op.getarglist()
        arglocs = [resloc, srcloc, imm(residx), imm(index.value), imm(count.value), imm(size)]
        self.perform(op, arglocs, resloc)

    consider_vec_float_unpack = consider_vec_int_unpack

    def consider_vec_float_expand(self, op):
        result = op.result
        assert isinstance(result, BoxVector)
        arg = op.getarg(0)
        args = op.getarglist()
        if isinstance(arg, Const):
            resloc = self.xrm.force_allocate_reg(result)
            srcloc = self.xrm.expand_float(result.getsize(), arg)
        else:
            resloc = self.xrm.force_result_in_reg(op.result, arg, args)
            srcloc = resloc

        size = op.result.getsize()
        self.perform(op, [srcloc, imm(size)], resloc)

    def consider_vec_int_expand(self, op):
        arg = op.getarg(0)
        args = op.getarglist()
        if isinstance(arg, Const):
            srcloc = self.rm.convert_to_imm(arg)
        else:
            srcloc = self.make_sure_var_in_reg(arg, args)
        resloc = self.xrm.force_allocate_reg(op.result, args)
        assert isinstance(op.result, BoxVector)
        size = op.result.getsize()
        self.perform(op, [srcloc, imm(size)], resloc)

    def consider_vec_int_signext(self, op):
        args = op.getarglist()
        resloc = self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
        sizearg = op.getarg(0)
        result = op.result
        assert isinstance(sizearg, BoxVector)
        assert isinstance(result, BoxVector)
        size = sizearg.getsize()
        tosize = result.getsize()
        self.perform(op, [resloc, imm(size), imm(tosize)], resloc)

    def consider_vec_box(self, op):
        # pseudo instruction, needed to create a new variable
        self.xrm.force_allocate_reg(op.result)

    def consider_guard_early_exit(self, op):
        pass

    def consider_vec_cast_float_to_int(self, op):
        args = op.getarglist()
        srcloc = self.make_sure_var_in_reg(op.getarg(0), args)
        resloc = self.xrm.force_result_in_reg(op.result, op.getarg(0), args)
        self.perform(op, [srcloc], resloc)

    consider_vec_cast_int_to_float = consider_vec_cast_float_to_int
    consider_vec_cast_float_to_singlefloat = consider_vec_cast_float_to_int
    consider_vec_cast_singlefloat_to_float = consider_vec_cast_float_to_int
