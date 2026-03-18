from processors.text_ops import reverse_text, to_uppercase
from processors.stats import word_count, char_count


def run(user_input: str) -> str:
    text = user_input.strip()
    lines = [
        f"Original   : {text}",
        f"Uppercase  : {to_uppercase(text)}",
        f"Reversed   : {reverse_text(text)}",
        f"Word count : {word_count(text)}",
        f"Char count : {char_count(text)}",
    ]
    return "\n".join(lines)
