### Added

- The chat test script (`tests/test_chat_script.py`, `tests/chat_questions.json`): 10 owner questions in English and Greek with the figures a right answer gives, derived from the seed month; the default run checks the tools give them on each question's day and that a question no tool answers gets "I don't know", and a stage rehearsal through the real agent runs when `ANTHROPIC_API_KEY` is set (#17)
