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


def format_time_input_code(time_ms: int | None) -> str:
    normalized_ms = normalize_time_to_centiseconds_ms(time_ms)

    if normalized_ms is None:
        return ""

    total_centiseconds = normalized_ms // 10
    minutes = total_centiseconds // 6000
    seconds = (total_centiseconds % 6000) // 100
    hundredths = total_centiseconds % 100

    return f"{minutes:02d}{seconds:02d}{hundredths:02d}"


def parse_time_input_to_ms(value: str | None) -> int | None:
    if value is None:
        return None

    raw_value = str(value).strip()

    if raw_value == "":
        return None

    if ":" in raw_value or "," in raw_value or "." in raw_value:
        normalized = raw_value.replace(".", ",")

        if ":" not in normalized or "," not in normalized:
            raise ValueError("Zeit bitte als MMSSHH oder MM:SS,HH eingeben.")

        minute_part, rest = normalized.split(":", 1)
        second_part, hundredth_part = rest.split(",", 1)
    else:
        digits = "".join(character for character in raw_value if character.isdigit())

        if len(digits) > 6:
            raise ValueError("Zeit bitte sechsstellig als MMSSHH eingeben.")

        digits = digits.zfill(6)
        minute_part = digits[0:2]
        second_part = digits[2:4]
        hundredth_part = digits[4:6]

    minutes = int(minute_part)
    seconds = int(second_part)
    hundredths = int(hundredth_part)

    if seconds > 59 or hundredths > 99:
        raise ValueError("Zeit bitte als MMSSHH oder MM:SS,HH eingeben.")

    return ((minutes * 60 + seconds) * 100 + hundredths) * 10
