---
name: waitrose-cli
description: Interact with Waitrose & Partners grocery services using the waitrose CLI. Use when users want to manage their Waitrose account, view/modify shopping trolley, check delivery slots, view orders, or manage addresses. Triggers on mentions of Waitrose, grocery shopping, delivery slots, trolley/cart management, or order history.
---

# Waitrose CLI

CLI tool for interacting with Waitrose & Partners grocery API.

## Important: Always Use JSON Output

Always prefix commands with `WAITROSE_JSON_OUTPUT=true` for comprehensive, parseable output:

```bash
WAITROSE_JSON_OUTPUT=true waitrose <command>
```

## Authentication

Always check `waitrose status` first. If already logged in, skip login.

```bash
# Check session status first
waitrose status

# Login only if not already authenticated
waitrose login -u email@example.com -p password

# Logout
waitrose logout
```

Session stored in `~/.waitrose/session.json` and auto-refreshes.

## Searching Products

Search for products before adding to trolley:

```bash
# Search with JSON output to get product IDs
waitrose search "organic eggs" --json

# Limit results
waitrose search "sirloin steak" -l 5 --json
```

The search results include:
- `id`: The product-id needed for adding to trolley (e.g., `"468390-810671-810672"`)
- `lineNumber`: First part of the product-id
- `name`: Product name
- `displayPrice`: Price display (e.g., `"£3.50"` or `"per kg"`)
- `size`: Package size

## Trolley Management

### View Trolley

```bash
waitrose trolley
waitrose trolley --json
```

### Add Items

Add items using the product-id from search results:

```bash
# Add a single item
waitrose trolley add <product-id>

# Add with quantity
waitrose trolley add 468390-810671-810672 --qty 2

# Example workflow
waitrose search "organic broccoli" --json   # Find product-id
waitrose trolley add 477602-428563-67380    # Add to trolley
```

| Flag | Default | Description |
|------|---------|-------------|
| `--qty` | 1 | Quantity to add |
| `--uom` | C62 | Unit of measure (C62=each, KGM=per kg) |

### Update Item Quantity

```bash
# Set item to specific quantity
waitrose trolley update 468390-810671-810672 --qty 2

# For weighted items
waitrose trolley update 468390-810671-810672 --qty 0.5 --uom KGM
```

| Flag | Default | Description |
|------|---------|-------------|
| `--qty` | required | New quantity to set |
| `--uom` | C62 | Unit of measure (C62=each, KGM=per kg) |

### Remove Items

```bash
# Remove item entirely
waitrose trolley remove 468390-810671-810672

# Remove specific quantity (reduces from current amount)
waitrose trolley remove 468390-810671-810672 --qty 1

# For weighted items
waitrose trolley remove 468390-810671-810672 --qty 0.5 --uom KGM
```

| Flag | Default | Description |
|------|---------|-------------|
| `--qty` | 0 | Quantity to remove (0 = remove all) |
| `--uom` | item's UOM | Unit of measure |

## Addresses

Get address IDs before working with delivery slots:

```bash
# List saved addresses (get address-id for delivery slots)
waitrose address list
```

## Delivery Slots

For delivery slots, `--address` is required:

```bash
# List available delivery slots (--address required)
waitrose slot list --address <address-id>
waitrose slot list --address <address-id> --days 7 --from 2025-01-01

# Book a slot
waitrose slot book <slot-id> --address <address-id>

# View current booked slot
waitrose slot get

# Cancel slot reservation
waitrose slot cancel
waitrose slot cancel <reservationId>
```

### Slot List Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | - | Delivery address ID (required for delivery) |
| `--days` | 7 | Number of days to fetch (max 7) |
| `--from` | today | Start date (YYYY-MM-DD) |
| `--slot-type` | DELIVERY | Slot type |
| `--grid` | DEFAULT_GRID | Slot grid type |

## Orders

```bash
# List recent orders
waitrose order list -l 10

# View order details
waitrose order get <order-number>
```

## Version

```bash
waitrose version
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--api-key` | Waitrose API key (or `WAITROSE_API_KEY` env) |
| `-j, --json` | Output in JSON format |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `WAITROSE_API_KEY` | API key (optional, has default) |
| `WAITROSE_USERNAME` | Email for login |
| `WAITROSE_PASSWORD` | Password for login |
| `WAITROSE_JSON_OUTPUT` | Set to "true" for JSON output by default |

## Troubleshooting

### "failed to add item: item may be out of stock or unavailable for delivery (500)"

This error occurs when:
1. **Item is out of stock** - Search for alternatives
2. **Variable weight item** - Items priced "per kg" (whole lamb legs, fish counters) often fail. Look for fixed-weight packaged alternatives instead
3. **No delivery slot booked** - Some items require an active slot reservation

### "invalid product-id format"

Product IDs must be in format `lineNumber-xxx-xxx` (e.g., `468390-810671-810672`). Get the full ID from search results with `--json` flag.

### Items not appearing in trolley

After adding, verify with `waitrose trolley --json`. The add command may report success but the API can silently reject items (especially variable-weight products).

### Slot booking fails

- Ensure you have a valid address ID (`waitrose address list`)
- Check slot availability first (`waitrose slot list`)

## Completing the Shopping Experience

After finishing a grocery shopping session (adding items to trolley, booking a slot, etc.), always show the user the trolley link so they can review and checkout:

```
https://www.waitrose.com/ecom/shop/trolley
```

This allows users to verify their order, apply promotions, and complete checkout on the Waitrose website.
