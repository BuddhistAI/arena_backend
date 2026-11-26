"""Form errors: how the arena reports what is wrong with an upload.

Long error lists are capped; ``unlisted_problems`` is the last error of a
capped list, so the uploader knows the list is incomplete.
"""


def unlisted_problems(count: int) -> str:
    """The message saying how many problems a capped list leaves out.

    Args:
        count: How many problems were found beyond the listed ones.

    Returns:
        A sentence naming the count.
    """
    noun = "problem was" if count == 1 else "problems were"
    return f"{count} more {noun} found and not listed; fix these and upload again to see them"
