import itertools

from vyper.ast.signatures.function_signature import FunctionSignature
from vyper.exceptions import (
    StateAccessViolation,
    StructureException,
    TypeCheckFailure,
)
from vyper.old_codegen.abi import abi_decode
from vyper.old_codegen.lll_node import LLLnode, push_label_to_stack
from vyper.old_codegen.parser_utils import getpos, pack_arguments
from vyper.old_codegen.types import (
    BaseType,
    ByteArrayLike,
    ListType,
    TupleLike,
    get_size_of_type,
    get_static_size_of_type,
    has_dynamic_data,
)


_label_counter = 0
# TODO a more general way of doing this
def _generate_label(name: str) -> str:
    _label_counter += 1
    return f"label{_label_counter}"

def make_call(stmt_expr, context):
    pos = getpos(stmt_expr)

    # ** Internal Call **
    # Steps:
    # (x) copy arguments into the soon-to-be callee
    # (x) allocate return buffer
    # (x) push jumpdest (callback ptr), then return buffer location
    # (x) jump to label

    method_name = stmt_expr.func.attr

    args_lll = Expr(x, self.context).lll_node for x in stmt_expr.args
    # TODO handle default args
    args_tuple_t = TupleType([x.typ for x in args_lll])
    args_as_tuple = LLLnode.from_list(["multi"] + [x for x in args_lll], typ=args_tuple_t)

    sig = FunctionSignature.lookup_sig(context.sigs, method_name, args_lll, stmt_expr, context)

    # register callee to help calculate our starting frame offset
    context.register_callee(sig.frame_size)

    if context.is_constant() and sig.mutability not in ("view", "pure"):
        raise StateAccessViolation(
            f"May not call state modifying function "
            f"'{method_name}' within {context.pp_constancy()}.",
            getpos(stmt_expr),
        )

    if not sig.internal:
        raise StructureException("Cannot call external functions via 'self'", stmt_expr)

    return_label = _generate_label(f"{sig.internal_function_label}_call")

    # allocate space for the return buffer
    # TODO allocate in stmt and/or expr.py
    return_buffer = context.new_internal_variable(sig.return_type) if sig.return_type is not None else "pass"
    return_buffer = LLLnode.from_list([return_buffer], annotation=f"{return_label}_return_buf")

    copy_args = make_setter(sig.frame_start, args_as_tuple, "memory", pos)

    call_sequence = [
            "seq_unchecked",
            copy_args,
            push_label_to_stack(return_label), # pass return label to subroutine
            return_buffer, # pass return buffer to subroutine
            ["goto", sig.internal_function_label]
            return_label,
            "jumpdest",
            return_buffer, # push return buffer location to stack
        ]

    o = LLLnode.from_list(
        call_sequence,
        typ=sig.output_type,
        location="memory",
        pos=pos,
        annotation=f"Internal Call: {method_name}",
        add_gas_estimate=sig.gas,
    )
    return o
