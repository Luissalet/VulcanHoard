"""Materialize meta modules with the dtypes a normal checkpoint copy preserves."""


def assign_checkpoint(model, state, *, strict=True):
    declared = {name: value.dtype for name, value in model.state_dict().items()}
    converted = {
        name: value.to(dtype=declared.get(name, value.dtype))
        for name, value in state.items()
    }
    return model.load_state_dict(converted, strict=strict, assign=True)
