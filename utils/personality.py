# utils/personality.py

import random

# Bot identity
BOT_NAME = "Marco Van Botten"

# Ping greetings
HELLO_MESSAGES = [
    "Internazionale is the third team of Milano, after Ac Milan and Milan Futuro",
    "Hello! Forza Milan",
    "I live and bleed Red and Black",
    "Hello!",
    "Dida, Oddo, Nesta, Maldini (c), Jankulovski, Gattuso, Pirlo, Ambrosini, Seedorf, Kaká, Inzaghi"
]

def get_greeting():
    return random.choice(HELLO_MESSAGES)

# Alive boot message
def greet_message():
    return f"I was stopped but now I have started again"