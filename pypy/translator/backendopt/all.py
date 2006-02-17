from pypy.translator.backendopt.raisingop2direct_call import raisingop2direct_call
from pypy.translator.backendopt.removenoops import remove_same_as
from pypy.translator.backendopt.inline import auto_inlining
from pypy.translator.backendopt.malloc import remove_simple_mallocs
from pypy.translator.backendopt.ssa import SSI_to_SSA
from pypy.translator.backendopt.propagate import propagate_all
from pypy.translator.backendopt.merge_if_blocks import merge_if_blocks
from pypy.translator import simplify
from pypy.translator.backendopt.escape import malloc_to_stack
from pypy.translator.backendopt.support import log

def backend_optimizations(translator, raisingop2direct_call_all=False,
                                      inline_threshold=1,
                                      mallocs=True,
                                      ssa_form=True,
                                      merge_if_blocks_to_switch=True,
                                      propagate=False,
                                      heap2stack=False):

    if raisingop2direct_call_all:
        raisingop2direct_call(translator)

    # remove obvious no-ops
    for graph in translator.graphs:
        remove_same_as(graph)
        simplify.eliminate_empty_blocks(graph)
        simplify.transform_dead_op_vars(graph, translator)

    # ...
    if propagate:
        propagate_all(translator)

    # inline functions in each other
    if inline_threshold:
        auto_inlining(translator, inline_threshold)

    # vaporize mallocs
    if mallocs:
        tot = 0
        for graph in translator.graphs:
            count = remove_simple_mallocs(graph)
            if count:
                # remove typical leftovers from malloc removal
                remove_same_as(graph)
                simplify.eliminate_empty_blocks(graph)
                simplify.transform_dead_op_vars(graph, translator)
                tot += count
        log.malloc("removed %d simple mallocs in total" % tot)
    if propagate:
        propagate_all(translator)

    if heap2stack:
        malloc_to_stack(translator)

    if merge_if_blocks_to_switch:
        for graph in translator.graphs:
            merge_if_blocks(graph)
   
    if ssa_form:
        for graph in translator.graphs:
            SSI_to_SSA(graph)

    translator.checkgraphs()
