#include "src_stm/stmgcintf.h"

__thread struct stm_thread_local_s stm_thread_local;

extern Signed pypy_stmcb_size(void*);
extern void pypy_stmcb_trace(void*, void(*)(void*));

inline size_t stmcb_size(struct object_s *obj) {
    return pypy_stmcb_size(obj);
}

inline void stmcb_trace(struct object_s *obj, void visit(object_t **)) {
    pypy_stmcb_trace(obj, (void(*)(void*))visit);
}

/* "include" the stmgc.c file here */
#include "src_stm/stmgc.c"