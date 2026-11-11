# Project Context: Aria

## Product Identity
Aria is an AI-powered fashion stylist chat application. It provides users with personalized style advice and fashion guidance through a conversational interface.

## Target Audience
- End Users: Fashion-conscious individuals seeking style assistance.
- Developers: Third-party integrators using the Aria API to build fashion-related services.

## House Style
- Tone: Professional, helpful, and inspiring.
- Voice: Clear and concise. Avoid overly academic or dense technical jargon in user-facing guides.
- Formatting: Use active voice. Use bolding for UI elements and API endpoints.
- Model References: Do not hardcode specific model names (e.g., "Groq" or "Mistral") in the main documentation. Refer to them as "the AI model" or "the current provider" to ensure documentation remains valid across model migrations.

## Core Constraints
- User Privacy: Authentication is token-based. User identity is derived from the token, not from URL parameters.
- Performance: User-facing error messages should be friendly (e.g., "Aria is a bit busy") rather than raw technical errors.
