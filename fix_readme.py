import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# Fix Mojibake
text = text.replace("?\"", "-")
text = text.replace("A20%", "+/-20%") # use ascii +/-
text = text.replace("CAT_old", "C * T_old")
text = text.replace("dtA(UAAT_amb", "dt * (UA * T_amb")
text = text.replace("dtAUA", "dt * UA")
text = text.replace("A- MTBF", "* MTBF")
text = text.replace("^''", "-")
text = text.replace("�", "-") # replacement character

# Live link badge
badge = "\n[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://sih26060.streamlit.app)\n"
if badge not in text:
    text = text.replace("# Antarctic Station Digital Twin\n", "# Antarctic Station Digital Twin\n" + badge)

# Update Tests count
text = re.sub(r"70 tests, 8 files", "73 tests, 9 files", text)

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)