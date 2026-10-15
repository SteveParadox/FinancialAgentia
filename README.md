> **Current execution guide:** [Python backend setup](python-backend/README.md) and [end-to-end operations](python-backend/END_TO_END_OPERATIONS.md). Research now runs on the Python durable worker; the Bun/Ink client requires that backend. Older local-agent setup sections below describe earlier versions.

# FinancialAgentia

An autonomous AI-powered financial research agent that conducts deep, methodical analysis of stocks and companies. FinancialAgentia leverages multi-phase reasoning, iterative planning, and comprehensive financial data tools to answer complex financial questions with accuracy and depth.

**Status**: Dual-stack implementation (TypeScript primary, Python backend port in progress)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Features](#features)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Development](#development)
- [API Reference](#api-reference)
- [Financial Tools](#financial-tools)
- [Contributing](#contributing)

---

## Overview

FinancialAgentia is a specialized AI agent designed for financial research. It breaks down complex financial questions into manageable research tasks, executes them strategically using a suite of financial data tools, and synthesizes comprehensive answers.

### Key Capabilities

- **Multi-phase reasoning**: Understand → Plan → Execute → Reflect → Answer
- **Iterative refinement**: Reflection loop validates whether sufficient data has been gathered
- **Just-in-time tool selection**: Tools are selected dynamically at execution time based on task requirements
- **Contextual awareness**: Maintains conversation history for multi-turn interactions
- **Streaming responses**: Supports real-time streaming of analysis results
- **Rate limiting & resilience**: Built-in retry logic, circuit breakers, and rate limit handling

### Supported Models

- **OpenAI** (GPT-4, GPT-3.5-turbo, etc.)
- **Anthropic Claude** (Claude 3 family)
- **Google Gemini**
- **Ollama** (local or cloud-hosted)

---

## Architecture

### Phase-Based Execution Model

FinancialAgentia executes through a well-defined multi-phase pipeline:

```
┌─────────────────┐
│  1. UNDERSTAND  │  Extract intent & entities from user query
└────────┬────────┘
         │
    ┌────▼────┐
    │ Iterate │
    │ Loop    │
    │         │
    ├────┬────┤
    │ 2. PLAN    │  Create task list with dependencies
    ├────┬────┤
    │ 3. EXECUTE │  Run tasks with just-in-time tool selection
    ├────┬────┤
    │ 4. REFLECT │  Evaluate data sufficiency & decide on next iteration
    └────┬────┘
         │
    ┌────▼──────────┐
    │ 5. ANSWER      │  Synthesize final response from all results
    └────────────────┘
```

### Phase Details

#### 1. **Understand Phase**
- Analyzes the user's query in context of conversation history
- Extracts intent: What does the user want to accomplish?
- Identifies entities: Ticker symbols, companies, dates, metrics, time periods
- Normalizes company names to ticker symbols (e.g., "Apple" → "AAPL")
- Returns structured `Understanding` object with intent and entity list

#### 2. **Plan Phase**
- Creates a structured task list based on the query intent
- Defines task types: `use_tools` (call financial data tools) or `reason` (analytical reasoning)
- Establishes task dependencies for parallel execution
- Incorporates reflection feedback from previous iterations
- Generates unique task IDs to avoid collisions across multiple plan iterations

#### 3. **Execute Phase**
- Executes planned tasks sequentially or in parallel based on dependencies
- For `use_tools` tasks: Selects appropriate financial tools and calls them
- For `reason` tasks: Calls the LLM for analytical reasoning
- Collects results from each task
- Streams progress updates for UI feedback

#### 4. **Reflect Phase**
- Evaluates whether sufficient data has been gathered
- Determines if another iteration is needed
- Provides guidance to the next planning iteration
- Prevents excessive iterations (configurable max iterations, default: 5)
- Returns reflection results with confidence scores

#### 5. **Answer Phase**
- Synthesizes a comprehensive answer from all task results
- Uses conversation context and tool results as evidence
- Streams the response back to the user
- Includes source citations and data references

---

## Project Structure

```
FinancialAgentia/
├── src/                           # TypeScript/React frontend & agent
│   ├── index.tsx                  # Entry point
│   ├── cli.tsx                    # CLI interface using Ink (React for terminal)
│   ├── theme.ts                   # Theme configuration
│   │
│   ├── agent/                     # Agent orchestration & phases
│   │   ├── orchestrator.ts        # Main agent class
│   │   ├── state.ts               # Type definitions
│   │   ├── schemas.ts             # Zod validation schemas
│   │   ├── prompts.ts             # System & user prompts
│   │   ├── task-executor.ts       # Task execution logic
│   │   ├── tool-executor.ts       # Tool invocation logic
│   │   └── phases/                # Phase implementations
│   │       ├── understand.ts
│   │       ├── plan.ts
│   │       ├── execute.ts
│   │       ├── reflect.ts
│   │       └── answer.ts
│   │
│   ├── model/                     # LLM integration
│   │   └── llm.ts                 # Multi-provider LLM wrapper
│   │
│   ├── tools/                     # Tool implementations
│   │   ├── index.ts               # Tool registry
│   │   ├── types.ts               # Tool type definitions
│   │   └── finance/               # Financial data tools
│   │       ├── api.ts             # API client
│   │       ├── fundamentals.ts    # Income statements, balance sheets, cash flows
│   │       ├── filings.ts         # SEC filings (10-K, 10-Q, 8-K)
│   │       ├── prices.ts          # Historical & current prices
│   │       ├── metrics.ts         # Financial metrics (P/E, ROE, etc.)
│   │       ├── news.ts            # Company news
│   │       ├── estimates.ts       # Analyst estimates & targets
│   │       ├── segments.ts        # Business segment data
│   │       ├── crypto.ts          # Cryptocurrency prices
│   │       ├── insider_trades.ts  # Insider trading data
│   │       └── constants.ts       # API constants
│   │
│   ├── components/                # React/Ink UI components
│   │   ├── Intro.tsx              # Welcome screen
│   │   ├── Input.tsx              # User input handler
│   │   ├── AnswerBox.tsx          # Answer display
│   │   ├── ModelSelector.tsx      # Model selection UI
│   │   ├── ApiKeyPrompt.tsx       # API key input
│   │   ├── QueueDisplay.tsx       # Query queue display
│   │   ├── AgentProgressView.tsx  # Progress tracking
│   │   ├── TaskListView.tsx       # Task list display
│   │   ├── StatusMessage.tsx      # Status updates
│   │   └── index.ts               # Component exports
│   │
│   ├── hooks/                     # React hooks
│   │   ├── useAgentExecution.ts   # Agent execution logic
│   │   ├── useApiKey.ts           # API key management
│   │   └── useQueryQueue.ts       # Query queue management
│   │
│   ├── utils/                     # Utilities
│   │   ├── config.ts              # Config management
│   │   ├── context.ts             # Tool context management
│   │   ├── env.ts                 # Environment variable helpers
│   │   ├── message-history.ts     # Conversation history
│   │   └── index.ts               # Utility exports
│   │
│   └── cli/                       # CLI-specific types
│       └── types.ts
│
├── python-backend/                # Python backend (port in progress)
│   ├── app/                       # FastAPI application
│   │   └── main.py                # FastAPI server with endpoints
│   │
│   ├── dexter_py/                 # Python agent implementation
│   │   ├── __init__.py
│   │   ├── file_reader.py         # File I/O utilities
│   │   │
│   │   ├── model/                 # LLM integration
│   │   │   └── llm.py             # Multi-provider LLM wrapper
│   │   │
│   │   ├── agent/                 # Agent orchestration
│   │   │   ├── orchestrator.py    # Main agent class
│   │   │   ├── state.py           # State types
│   │   │   ├── schemas.py         # Pydantic models
│   │   │   ├── prompts.py         # System prompts
│   │   │   ├── task_executor.py   # Task execution
│   │   │   ├── tool_executor.py   # Tool invocation
│   │   │   └── phases/            # Phase implementations
│   │   │       ├── understand.py
│   │   │       ├── plan.py
│   │   │       ├── execute.py
│   │   │       ├── reflect.py
│   │   │       └── answer.py
│   │   │
│   │   ├── tools/                 # Tool registry
│   │   │   └── __init__.py        # Empty tools list (to be ported)
│   │   │
│   │   ├── utils/                 # Utilities
│   │   │   ├── context.py         # Tool context manager
│   │   │   └── message_history.py # Conversation history
│   │   │
│   │   └── __pycache__/
│   │
│   ├── tests/                     # Unit tests
│   │   ├── test_api.py
│   │   ├── test_backend.py
│   │   └── test_llm.py
│   │
│   ├── cli.py                     # CLI entry point (Typer)
│   ├── requirements.txt           # Python dependencies
│   ├── pyproject.toml             # Project metadata
│   ├── README.md                  # Backend-specific README
│   └── python_backend_logging.py  # Logging configuration
│
├── package.json                   # Node.js dependencies (TypeScript/React)
├── tsconfig.json                  # TypeScript configuration
├── jest.config.js                 # Jest testing configuration
├── env.example                    # Example environment variables
├── .env                           # Local environment variables (git-ignored)
└── bun.lock                       # Bun lock file

```

---

## Features

### Agent Capabilities

- **Query Understanding**: Extracts intent and entities from natural language queries
- **Multi-turn Conversations**: Maintains context across multiple queries
- **Intelligent Planning**: Creates task lists with proper dependencies and typing
- **Tool Integration**: Seamlessly integrates financial data tools into execution
- **Iterative Refinement**: Reflects on results and iterates until sufficient data is gathered
- **Streaming Responses**: Real-time answer streaming with progress updates
- **Error Recovery**: Graceful handling of tool failures with retry logic

### Financial Data Coverage

The agent can analyze:
- **Fundamentals**: Income statements, balance sheets, cash flow statements
- **Pricing**: Historical prices, technical analysis, price targets
- **Filings**: SEC filings (10-K, 10-Q, 8-K, S-1, etc.)
- **Metrics**: P/E ratios, ROE, ROA, debt ratios, growth metrics
- **News**: Company news, earnings releases, events
- **Estimates**: Analyst estimates, price targets, earnings surprises
- **Segments**: Business segment performance and revenue breakdown
- **Crypto**: Cryptocurrency prices and market data
- **Insider Trading**: Insider buy/sell activity and holdings

---

## Installation & Setup

### Prerequisites

- **Node.js** 18+ or **Bun** (TypeScript/frontend)
- **Python** 3.10+ (Python backend)
- API keys for at least one supported LLM provider:
  - `OPENAI_API_KEY` for OpenAI models
  - `ANTHROPIC_API_KEY` for Claude models
  - `GOOGLE_API_KEY` for Google Gemini
  - `OLLAMA_API_KEY` for Ollama (cloud) or local Ollama instance

### TypeScript/Frontend Setup

```bash
# Install dependencies (using Bun)
bun install

# Or using npm
npm install

# Create .env file with API keys
cp env.example .env
# Edit .env with your API keys

# Run the CLI
bun run src/index.tsx

# Or in development mode with auto-reload
bun --watch run src/index.tsx

# Type checking
bun run typecheck

# Testing
bun test
```

### Python Backend Setup

```bash
# Navigate to backend directory
cd python-backend

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows:
.venv\Scripts\Activate.ps1
# On macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file in project root
cp ..env.example ../.env
# Edit with your API keys

# Run the FastAPI server
python -m uvicorn app.main:app --reload

# Or use the CLI
python cli.py ask "What is the date today?"
```

---

## Usage

### CLI Interface (TypeScript)

```bash
# Start the interactive CLI
bun run src/index.tsx

# The CLI will:
# 1. Show an introduction
# 2. Prompt for model selection
# 3. Verify API keys
# 4. Accept user queries
# 5. Display real-time progress as the agent researches
# 6. Show final answer with sources
```

### CLI Commands (Python)

```bash
# Ask a simple question
python cli.py ask "What is the P/E ratio of Apple?"

# The response will use the configured LLM (default: local Ollama)
```

### API Endpoints (Python Backend)

#### POST `/chat`
Stream a financial analysis query.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the key metrics for AAPL?"}' \
  --no-buffer
```

Response: Server-sent events stream with progress updates and final answer.

#### GET `/health`
Health check endpoint.

```bash
curl http://localhost:8000/health
```

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
# LLM Provider Configuration
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
OLLAMA_API_KEY=...  # For Ollama cloud
OLLAMA_BASE_URL=http://localhost:11434  # For local Ollama

# Financial Data API
FINANCIAL_DATASETS_API_KEY=...

# Optional: Model selection (default: ollama-deepseek-v3.1:671b-cloud)
DEFAULT_MODEL=gpt-4
DEFAULT_PROVIDER=openai

# Optional: Agent configuration
MAX_ITERATIONS=5
DEBUG=false
```

### Supported Models

**OpenAI**:
- `gpt-4-turbo-preview`
- `gpt-4`
- `gpt-3.5-turbo`

**Anthropic**:
- `claude-3-opus`
- `claude-3-sonnet`
- `claude-3-haiku`

**Google**:
- `gemini-2.0-pro`
- `gemini-1.5-pro`
- `gemini-1.5-flash`

**Ollama**:
- `ollama-<model-name>` (e.g., `ollama-deepseek-v3.1:671b-cloud`)
- Prefix with `-cloud` for cloud-hosted Ollama

---

## Development

### Project Stack

**Frontend/TypeScript:**
- **Framework**: React + Ink (terminal UI)
- **Language**: TypeScript 5.x
- **Runtime**: Bun or Node.js
- **Validation**: Zod (schema validation)
- **LLM Integration**: LangChain

**Backend/Python:**
- **Framework**: FastAPI + Uvicorn
- **Language**: Python 3.10+
- **CLI**: Typer
- **LLM Integration**: LangChain
- **Logging**: Structlog
- **Rate Limiting**: SlowAPI
- **Monitoring**: Prometheus

### Type Definitions

#### Understanding Type

```typescript
interface Understanding {
  intent: string;
  entities: Array<{
    type: 'ticker' | 'date' | 'metric' | 'company' | 'period' | 'other';
    value: string;
  }>;
}
```

#### Plan Type

```typescript
interface Plan {
  summary: string;
  tasks: Array<{
    id: string;
    description: string;
    status: 'pending' | 'in_progress' | 'completed' | 'failed';
    taskType: 'use_tools' | 'reason';
    dependsOn: string[];
  }>;
}
```

#### Task Execution

```typescript
interface TaskResult {
  taskId: string;
  status: 'completed' | 'failed';
  toolCalls: ToolCallStatus[];
  result: Record<string, unknown>;
}
```

### Testing

```bash
# TypeScript tests
bun test

# Watch mode
bun test --watch

# Python tests
cd python-backend
pytest tests/
pytest tests/ -v  # Verbose
```

### Code Style

- **TypeScript**: ESLint configured, follows standard patterns
- **Python**: PEP 8 style guide
- **Imports**: Organized and grouped appropriately

---

## API Reference

### Agent Orchestrator

#### TypeScript: `Agent` class

```typescript
const agent = new Agent({
  model: 'gpt-4',
  maxIterations: 5,
  callbacks: {
    onPhaseStart: (phase) => console.log(`Starting ${phase}`),
    onPhaseComplete: (phase) => console.log(`Completed ${phase}`),
    onUnderstandingComplete: (understanding) => { /* ... */ },
    onAnswerStream: (stream) => { /* ... */ },
  },
});

const answer = await agent.run(query, messageHistory);
```

#### Python: `Agent` class

```python
from dexter_py.agent.orchestrator import Agent, AgentOptions, AgentCallbacks

agent = Agent(AgentOptions(
    model='gpt-4',
    max_iterations=5,
    callbacks=AgentCallbacks(
        on_phase_start=lambda phase: print(f'Starting {phase}'),
        on_understanding_complete=lambda u: print(u),
    )
))

answer = await agent.run('What is the P/E ratio of AAPL?')
```

### LLM Interface

#### TypeScript: `callLlm()`

```typescript
const result = await callLlm(prompt, {
  model: 'gpt-4',
  systemPrompt: 'You are a financial expert.',
  outputSchema: MySchema,  // Optional Zod schema
  tools: [tool1, tool2],   // Optional tool definitions
});
```

#### Python: `call_llm()`

```python
from dexter_py.model.llm import call_llm

result = await call_llm(
    prompt='Analyze this stock...',
    model='gpt-4',
    system_prompt='You are a financial expert.',
    output_model=MyPydanticModel,  # Optional
    tools=[tool1, tool2],  # Optional
)
```

---

## Financial Tools

### Available Tools

All tools are implemented in `src/tools/finance/`:

| Tool | Function | Use Case |
|------|----------|----------|
| `get_income_statements` | Fetch income statements | Profitability analysis |
| `get_balance_sheets` | Fetch balance sheets | Financial position assessment |
| `get_cash_flow_statements` | Fetch cash flow statements | Liquidity analysis |
| `get_all_financial_statements` | Fetch all three statements | Comprehensive fundamentals |
| `get_prices` | Historical & current prices | Price analysis & technical signals |
| `getFinancialMetrics` | P/E, ROE, debt ratios, etc. | Valuation & efficiency metrics |
| `getNews` | Company news | Recent developments & events |
| `getAnalystEstimates` | Earnings estimates & targets | Market expectations |
| `getSegmentedRevenues` | Business segment data | Segment-level performance |
| `getCryptoPriceSnapshot` / `getCryptoPrices` | Crypto prices | Digital asset analysis |
| `getInsiderTrades` | Insider trading data | Insider sentiment |
| `get10KFilingItems` / `get10QFilingItems` / `get8KFilingItems` | SEC filings | Detailed regulatory disclosures |

### Tool Usage Example

```typescript
const tool = getIncomeStatements;
const result = await tool.invoke({
  ticker: 'AAPL',
  period: 'annual',
  limit: 5,
});
```

---

## Performance & Resilience

### Built-in Features

- **Retry Logic**: Exponential backoff with jitter for transient failures
- **Circuit Breakers**: Prevents cascading failures when APIs are down
- **Rate Limiting**: Respects API rate limits with automatic backoff
- **Timeouts**: Configurable timeouts prevent hanging requests
- **Error Tracking**: Detailed error logging for debugging
- **Structured Logging**: JSON-formatted logs for monitoring

### Configuration

```python
# In python-backend/dexter_py/model/llm.py
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(RateLimitError),
)
async def call_llm(...):
    # Automatically retries on RateLimitError
```

---

## Contributing

### Adding New Tools

1. Create tool function in `src/tools/finance/[category].ts`
2. Use LangChain's `DynamicStructuredTool` wrapper
3. Add schema validation with Zod
4. Export from `src/tools/finance/index.ts`
5. Tool is automatically available to the agent

Example:

```typescript
export const getMyMetric = new DynamicStructuredTool({
  name: 'get_my_metric',
  description: 'Fetches my metric from the API',
  schema: z.object({
    ticker: z.string().describe('Stock ticker'),
  }),
  func: async (input) => {
    const { data, url } = await callApi('/endpoint', input);
    return formatToolResult(data, [url]);
  },
});
```

### Adding New LLM Providers

1. Install provider package (e.g., `@langchain/gemini`)
2. Add conditional import in `src/model/llm.ts`
3. Add provider factory in `getChatModel()`
4. Test with sample queries
5. Document in configuration section

---

## Troubleshooting

### Common Issues

**"API key not found" error**
- Check `.env` file exists and contains correct API key
- Verify environment variables are loaded: `console.log(process.env.OPENAI_API_KEY)`
- Ensure no extra spaces or quotes in `.env`

**"Module not found" errors**
- Run `bun install` (TypeScript) or `pip install -r requirements.txt` (Python)
- Clear node_modules/venv and reinstall

**Slow agent responses**
- Check internet connection
- Verify API rate limits not exceeded
- Try with a faster model (e.g., GPT-3.5-turbo instead of GPT-4)

**Tool failures**
- Verify `FINANCIAL_DATASETS_API_KEY` is set
- Check API key has required permissions
- Review API documentation for parameter formats

### Debug Mode

```bash
# TypeScript
DEBUG=true bun run src/index.tsx

# Python
DEBUG=true python cli.py ask "your question"
```

---

## License

[Add appropriate license information]

---

## Support

For issues, questions, or feature requests, please open an issue on the project repository.

---

## Changelog

### v2.4.1 (Current)
- Dual-stack architecture with TypeScript primary and Python backend port
- Phase-based agent orchestration
- Multi-turn conversation support
- Streaming responses
- Multi-provider LLM support
- Comprehensive financial data tools

### v2.0.0
- TypeScript implementation of core agent
- CLI interface with Ink React
- Tool executor with just-in-time selection

### v1.0.0
- Initial architecture design
- Basic agent phases

---

## Acknowledgments

This project is built on:
- [LangChain](https://langchain.com/) for LLM orchestration
- [OpenAI](https://openai.com/), [Anthropic](https://anthropic.com/), and other LLM providers
- [Financial Datasets API](https://financialdatasets.ai/) for financial data
- [Ink](https://github.com/vadimdemedes/ink) for terminal UI
- [FastAPI](https://fastapi.tiangolo.com/) for backend API

