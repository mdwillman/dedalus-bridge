# Dedalus Bridge

A lightweight FastAPI-based service that sits between client applications (such as mobile or web apps) and your AI infrastructure. Routes requests through the [Dedalus SDK for Python](https://github.com/dedalus-labs/dedalus-sdk-python) *or* directly to MCP-compatible tools and agents hosted on Google Cloud Run.

Provides an endpoint for relaying model prompts and completions through Dedalus’s hosted API.

Also serves as an authentication and policy layer. Client apps (such as the Avalogica iOS app) authenticate via Firebase, and the bridge validates ID tokens, logs user context, and then forwards the request to the appropriate backend (Dedalus or one or more customized MCP servers). This makes Dedalus Bridge the single, secure entry point for AI functionality across various devices and services.

---

## Features

- Built with **FastAPI** for speed and clarity
- Uses the official **Dedalus SDK** for stable integration with Dedalus’s hosted models
- Can also call **MCP servers hosted on Google Cloud Run**, such as custom news and weather providers
- Acts as an **authentication and routing layer**, validating Firebase ID tokens and dispatching requests to the correct backend lane
- Ready for **Docker** and **Google Cloud Run** deployment
- Automatically loads environment variables via `.env`
- Simple REST endpoint for prompt-to-response communication

---

## Architecture Overview

[iOS App / Web App / Other Clients]
             ↓
      [Dedalus Bridge API]
    (Firebase auth, logging,
      routing & orchestration)
       ↙                ↘
[Dedalus SDK → Dedalus API]    [MCP Servers on Cloud Run]
                             (e.g., Avalogica AI News & Weather)

Securely routes requests from applications to either Dedalus’s hosted model API or your own MCP servers running on Cloud Run. Handles Firebase-based authentication, attaches the right credentials (such as API keys or service accounts), and ensures that clients only ever talk to a single, well-defined HTTP API surface.

---

## Installation (Local Development)

### 1. Clone the repository
```bash
git clone https://github.com/<your-username>/dedalus-bridge.git
cd dedalus-bridge
```
