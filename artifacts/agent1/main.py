from bs4 import BeautifulSoup
from processors.text_ops import reverse_text, to_uppercase
from processors.stats import word_count, char_count


def run(user_input: str) -> str:
    text = user_input.strip()
    clean_text = BeautifulSoup(text, "html.parser").get_text() if "<" in text else text
    lines = [
        f"Original   : {text}",
        f"Clean text : {clean_text}",
        f"Uppercase  : {to_uppercase(clean_text)}",
        f"Reversed   : {reverse_text(clean_text)}",
        f"Word count : {word_count(clean_text)}",
        f"Char count : {char_count(clean_text)}",
    ]
    return "\n".join(lines)
