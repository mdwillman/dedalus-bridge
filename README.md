# Dedalus Bridge

A lightweight FastAPI-based service that acts as a bridge between client applications (such as mobile or web apps) and the [Dedalus SDK for Python](https://github.com/dedalus-labs/dedalus-sdk-python).

Provides an endpoint for relaying model prompts and completions through Dedalus’s hosted API.

---

## Features

- Built with **FastAPI** for speed and clarity
- Uses the official **Dedalus SDK** for stable integration
- Ready for **Docker** and **Google Cloud Run** deployment
- Automatically loads environment variables via `.env`
- Simple REST endpoint for prompt-to-response communication

---

## Architecture Overview

[iOS App / Web App]
↓
[Dedalus Bridge API]
↓
[Dedalus SDK → Dedalus API]

Securely routes requests from your application to Dedalus’s hosted model API using your configured API key.

---

## Installation (Local Development)

### 1. Clone the repository
```bash
git clone https://github.com/<your-username>/dedalus-bridge.git
cd dedalus-bridge