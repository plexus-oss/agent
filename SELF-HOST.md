# Plexus Self-Host

Run Plexus on your own infrastructure with full control over your data.

## Cloud vs Self-Hosted

| | Plexus Cloud | Self-Hosted |
|---|---|---|
| **Setup** | `plexus login` | Docker Compose |
| **Data location** | Plexus servers | Your infrastructure |
| **Maintenance** | Managed by Plexus | You manage |
| **Best for** | Quick start, small teams | Enterprise, air-gapped, compliance |

**Important:** When you run `plexus login` (without `--endpoint`), your data goes to Plexus Cloud at `app.plexusaero.space`. For self-hosted, you must specify your own endpoint.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/plexus-oss/agent.git
cd agent

# Start the stack
docker compose up -d

# Connect your agent to YOUR instance (not Plexus Cloud)
pip install plexus-agent
plexus login --endpoint http://localhost:3000
```

The `--endpoint` flag is required for self-hosted. Without it, `plexus login` connects to Plexus Cloud.

## What's Included

| Service | Port | Description |
|---------|------|-------------|
| **Plexus Dashboard** | 3000 | Web UI and API |
| **PostgreSQL** | 5432 | Time-series data storage |
| **MQTT Broker** (optional) | 1883, 9001 | For sensor bridges |

## Connecting Agents

### To your self-hosted instance

```bash
# Option 1: Login with endpoint
plexus login --endpoint http://your-server:3000

# Option 2: Set endpoint, then login
plexus config set endpoint http://your-server:3000
plexus login

# Option 3: Environment variable
export PLEXUS_ENDPOINT=http://your-server:3000
plexus login
```

### Auto-discovery (LAN)

```bash
# Find local Plexus instances
plexus discover

# Auto-configure the first one found
plexus discover -a
```

### Verify your endpoint

```bash
# Check where your agent is pointing
plexus config

# Should show your self-hosted URL, not app.plexusaero.space
```

## Configuration

### Environment Variables

Create a `.env` file to customize:

```bash
# Database password (change in production!)
DB_PASSWORD=your_secure_password

# Public URL (for remote access)
PUBLIC_URL=http://your-server:3000

# Enable MQTT broker
COMPOSE_PROFILES=mqtt
```

### Default API Key

A default API key is created for quick testing:

```
plx_selfhost_default_key_12345678
```

**Change this in production!** Create new keys via the dashboard or:

```sql
-- Connect to PostgreSQL and insert a new key
INSERT INTO api_keys (org_id, name, key_prefix, key_hash, scopes)
VALUES (
  'default',
  'Production Key',
  'plx_prod',
  encode(sha256('your_new_key_here'::bytea), 'hex'),
  ARRAY['otlp:write', 'otlp:read']
);
```

## With MQTT Broker

Enable the MQTT broker for `plexus mqtt-bridge`:

```bash
docker compose --profile mqtt up -d
```

Then bridge your MQTT topics:

```bash
plexus mqtt-bridge -b localhost -t "sensors/#"
```

## Accessing from Other Machines

1. Set `PUBLIC_URL` in your `.env`:
   ```bash
   PUBLIC_URL=http://192.168.1.100:3000
   ```

2. Restart:
   ```bash
   docker compose down && docker compose up -d
   ```

3. Connect agents:
   ```bash
   plexus login --endpoint http://192.168.1.100:3000
   ```

## Data Persistence

Data is stored in Docker volumes:
- `postgres_data` - All telemetry and configuration
- `mqtt_data` - MQTT broker state (if enabled)

### Backup

```bash
# Backup PostgreSQL
docker compose exec db pg_dump -U plexus plexus > backup.sql

# Restore
docker compose exec -T db psql -U plexus plexus < backup.sql
```

## Upgrading

```bash
docker compose pull
docker compose up -d
```

## Troubleshooting

### Check logs
```bash
docker compose logs -f plexus
docker compose logs -f db
```

### Reset everything
```bash
docker compose down -v  # Warning: deletes all data
docker compose up -d
```

### Connection issues
```bash
# Test if Plexus is running
curl http://localhost:3000/api/ingest

# Check agent can reach it
plexus discover

# Verify agent config
plexus config
```

### "Data going to wrong place"

If your data is appearing in Plexus Cloud instead of your self-hosted instance:

```bash
# Check your current endpoint
plexus config

# If it shows app.plexusaero.space, reconfigure:
plexus config set endpoint http://your-server:3000
```

## Production Checklist

- [ ] Change `DB_PASSWORD` from default
- [ ] Create new API keys (don't use the default)
- [ ] Set up TLS/HTTPS (use a reverse proxy like nginx/caddy)
- [ ] Configure backups
- [ ] Set resource limits in docker-compose.yml
- [ ] Enable MQTT authentication if using the broker
- [ ] Verify all agents point to your instance, not Plexus Cloud

## Resource Requirements

**Minimum:**
- 1 CPU core
- 1 GB RAM
- 10 GB disk

**Recommended:**
- 2+ CPU cores
- 4 GB RAM
- SSD storage
- Scales with data volume and query complexity
