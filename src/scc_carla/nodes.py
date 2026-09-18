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


def parse_node_target(target: int | str | None) -> int:
    """Parses a target string or int into a target node ID.

    Returns:
        0 for bastion, 1-3 for cluster nodes. Defaults to 1 if None.

    Raises:
        ValueError: If target cannot be resolved.
    """
    match target:
        case None:
            return 1
        case 0 | "0" | "bastion" | "b":
            return 0
        case int(n) if n in (1, 2, 3):
            return n
        case str(s) if s.strip() in ("1", "2", "3"):
            return int(s.strip())
        case str(s) if s.strip().lower() in ("node1", "node2", "node3"):
            return int(s.strip().lower()[-1])
        case invalid:
            raise ValueError(
                f"Invalid target '{invalid}'. Expected 1, 2, 3 (or node1, node2, node3), or 'bastion'."
            )
