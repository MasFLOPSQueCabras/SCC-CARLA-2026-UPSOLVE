def resolve_target_nodes(node: int | None, all_nodes: bool) -> list[int]:
    """Resolves target node IDs using pattern matching.

    Returns:
        A list of node IDs (e.g. [1, 2, 3] or [1]) or [] if no target specified.

    Raises:
        ValueError: If node is outside the allowable range (1, 2, 3).
    """
    match (all_nodes, node):
        case (True, _):
            return [1, 2, 3]
        case (False, int(n)) if n in (1, 2, 3):
            return [n]
        case (False, None):
            return []
        case (False, invalid):
            raise ValueError(f"Invalid node ID {invalid}. Must be 1, 2, or 3.")
        case _:
            raise ValueError("Invalid node target specification.")
