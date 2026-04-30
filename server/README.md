# foreqcast-server

Minimal reference implementation of a forecast server for the Foreqcast ecosystem. Authenticates via API key and serves forecast Parquet files for download.

## Quickstart

```bash
cd server
uv sync

# Set required environment variables
export FOREQCAST_API_KEYS="my-secret-key"
export FOREQCAST_DIR="./forecasts"  # directory containing .parquet files

# Start the server
uv run foreqcast-server
```

The server starts on `http://0.0.0.0:8400` by default.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FOREQCAST_API_KEYS` | ✅ | — | Comma-separated list of valid API keys |
| `FOREQCAST_DIR` | ✅ | `./forecasts` | Directory containing forecast `.parquet` files |
| `FOREQCAST_HOST` | — | `0.0.0.0` | Bind host |
| `FOREQCAST_PORT` | — | `8400` | Bind port |

## API Endpoints

### `GET /health`
Healthcheck. No authentication required.

### `GET /forecasts`
List available forecast files. Requires `X-API-Key` header.

```bash
curl -H "X-API-Key: my-secret-key" http://localhost:8400/forecasts
```

### `GET /forecasts/latest`
Download the most recently modified forecast Parquet file.

```bash
curl -H "X-API-Key: my-secret-key" http://localhost:8400/forecasts/latest -o forecast.parquet
```

### `GET /forecasts/{filename}`
Download a specific forecast file by name.

```bash
curl -H "X-API-Key: my-secret-key" http://localhost:8400/forecasts/external_forecast.parquet -o forecast.parquet
```

## Expected Parquet Schema

Forecast files served must conform to the `EXTERNAL_FORECAST_SCHEMA`:

| Column | Type | Required |
|---|---|---|
| `_odoo_product_id` | `int64` | ✅ |
| `_odoo_warehouse_id` | `int64` | ✅ |
| `forecasted_daily_demand` | `float64` | ✅ |
| `confidence` | `string` | optional (defaults to `"external"`) |

## Client Usage

From the `foreqcast` CLI:

```bash
foreqcast input/ output/ \
    --forecast-source external \
    --external-uri http://localhost:8400/forecasts/latest \
    --external-api-key my-secret-key
```
