def normalize_time_to_centiseconds_ms(time_ms: int | None) -> int | None:
    if time_ms is None:
        return None

    time_ms = max(0, int(time_ms))
    return int((time_ms + 5) // 10) * 10


def format_time_ms(time_ms: int | None, empty_value: str = "—") -> str:
    if time_ms is None:
        return empty_value

    normalized_ms = normalize_time_to_centiseconds_ms(time_ms)

    if normalized_ms is None:
        return empty_value

    total_centiseconds = normalized_ms // 10
    minutes = total_centiseconds // 6000
    seconds = (total_centiseconds % 6000) // 100
    hundredths = total_centiseconds % 100

    return f"{minutes:02d}:{seconds:02d},{hundredths:02d}"
