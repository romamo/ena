### Fixed

- The owner sees only Ena's reply, never the model's narration of its steps: text the model writes next to a tool call ("I need the event id first.") is dropped, even when the SDK sends the text and the call as two messages, and the chat script's live rehearsal fails a reply that opens with narration
