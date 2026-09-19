def resolve_target_nodes(
    node: int | list[int] | None, all_nodes: bool = False
) -> list[int]:
    """Resolves target node IDs using pattern matching and validation.

    Args:
        node: A single node ID, a list of node IDs (e.g. from multiple -n flags), or None.
        all_nodes: If True, targets all cluster nodes [1, 2, 3].

    Returns:
        A sorted, deduplicated list of valid node IDs (e.g. [1, 2, 3] or [1, 2]) or [] if no target specified.

    Raises:
        ValueError: If any node ID is outside the allowable range (1, 2, 3).
    """
    if all_nodes:
        return [1, 2, 3]

    if node is None:
        return []

    if isinstance(node, int):
        if node in (1, 2, 3):
            return [node]
        raise ValueError(f"Invalid node ID {node}. Must be 1, 2, or 3.")

    if isinstance(node, list):
        if not node:
            return []
        resolved: set[int] = set()
        for n in node:
            if isinstance(n, int) and n in (1, 2, 3):
                resolved.add(n)
            else:
                raise ValueError(f"Invalid node ID {n}. Must be 1, 2, or 3.")
        return sorted(resolved)

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
