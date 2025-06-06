# utils/personality.py

import random

# Bot identity
BOT_NAME = "Marco Van Botten"

# Ping greetings
HELLO_MESSAGES = [
    "Did you know Internazionale is the third team of Milano, after Ac Milan and Milan Futuro?",
    "Hello! Forza Milan",
    "I live! And I bleed Red and Black",
    "Hello my Rossonero brother, what's up?",
    "After Istanbul, we had Athens",
    "Forza lotta, vincerai! Non ti lasceremo mai!",
    "It is true that I follow all the matches, but in truth, I am a Milanista.",
    "Forza Milan, louder today than yesterday, and louder tomorrow than today",
    "Dida, Oddo, Nesta, Maldini (c), Jankulovski, Gattuso, Pirlo, Ambrosini, Seedorf, Kaká, Inzaghi",
]

def get_greeting():
    return random.choice(HELLO_MESSAGES)

# Alive boot message
def greet_message():
    return f"I am back Online"