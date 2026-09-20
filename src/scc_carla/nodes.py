def resolve_target_nodes(nodes: list[int] | int | None = None) -> list[int]:
    """Resolves target node IDs using structural pattern matching and validation.

    Args:
        nodes: A single node ID, a list of node IDs, or None. If None or empty,
               defaults to targeting all cluster nodes [1, 2, 3].

    Returns:
        A sorted, deduplicated list of valid node IDs (e.g. [1, 2, 3] or [1, 2]).

    Raises:
        ValueError: If any node ID is outside the allowable range (1, 2, 3).
    """
    match nodes:
        case None | []:
            return [1, 2, 3]
        case int(n) if n in (1, 2, 3):
            return [n]
        case list() as items if items:
            resolved: set[int] = set()
            for item in items:
                match item:
                    case int(n) if n in (1, 2, 3):
                        resolved.add(n)
                    case invalid:
                        raise ValueError(f"Invalid node ID '{invalid}'. Must be 1, 2, or 3.")
            return sorted(resolved)
        case invalid:
            raise ValueError(f"Invalid node specification: '{invalid}'. Must be 1, 2, or 3.")


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
