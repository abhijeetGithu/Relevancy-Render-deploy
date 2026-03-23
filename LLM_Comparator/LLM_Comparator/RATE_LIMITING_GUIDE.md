# Google API Rate Limiting - User Guide

## Overview

Your LLM Comparator now includes a comprehensive rate limiting system to protect against excessive Google Custom Search API usage and billing. The system enforces daily and monthly query limits, per-run caps, and cooldown periods.

## Quick Start

### View Your Usage

1. Start the server: `python -m uvicorn app:app --reload --port 8000`
2. Open http://localhost:8000 in your browser
3. The **Google API Usage** dashboard will appear at the top of the page (when rate limiting is enabled)

### Dashboard Features

The dashboard shows:
- **Daily Queries**: Current usage out of 500 daily limit
- **Monthly Queries**: Current usage out of 5000 monthly limit
- **Runs Today**: Number of analysis runs completed (3 free per day)
- **Cooldown Timer**: Shows when you can run again (after 3 runs)
- **Reset Countdowns**: Time until daily/monthly limits reset

### Color Coding

- 🟢 **Green** (0-70%): Normal usage
- 🟡 **Yellow** (70-90%): Approaching limit
- 🔴 **Red** (90-100%): Near or at limit

## Configuration

All limits are configurable in `services/rate_limiter.py`:

```python
# Master toggle - set to False to disable all rate limiting
RATE_LIMITING_ENABLED = True

# Query limits
DAILY_QUERY_LIMIT = 500      # Max queries per day
MONTHLY_QUERY_LIMIT = 5000   # Max queries per month
MAX_QUERIES_PER_RUN = 100    # Max queries in a single analysis

# Cooldown settings
FREE_RUNS_PER_DAY = 3        # Free runs before cooldown kicks in
COOLDOWN_HOURS = 3           # Hours to wait after using free runs
```

### How to Adjust Limits

1. Open `services/rate_limiter.py`
2. Edit the values at the top of the file
3. Restart the server for changes to take effect

### How to Disable Rate Limiting

Set `RATE_LIMITING_ENABLED = False` in `services/rate_limiter.py`

**Note**: Even when disabled, the per-run cap (100 queries) is still enforced for billing protection.

## Usage Rules

### Daily Limit (500 queries)
- Resets every day at midnight
- Shared across all runs in a day
- Example: 5 runs × 100 queries = 500 queries used

### Monthly Limit (5000 queries)
- Resets on the 1st of each month
- Tracks cumulative usage across all days
- Example: 10 days × 500 queries = 5000 queries used

### Per-Run Cap (100 queries)
- **Always enforced**, even when rate limiting is disabled
- Prevents accidentally processing huge files
- If your file has more than 100 rows, you'll get an error

### Cooldown System
- First 3 runs per day: No cooldown
- After 3 runs: 3-hour cooldown before next run
- Cooldown resets at midnight
- Timer shows exactly when you can run again

## What Happens When Limits Are Hit

### Daily Limit Reached
```
❌ Daily query limit would be exceeded. 
   You have 20 queries remaining today. 
   Daily limit resets in 8.5 hours.
```

### Monthly Limit Reached
```
❌ Monthly query limit would be exceeded. 
   You have 150 queries remaining this month. 
   Monthly limit resets in 12 days.
```

### Cooldown Active
```
❌ Cooldown active. 
   You've used 3 of 3 free runs today. 
   Next run available in 2h 15m.
```

### Per-Run Cap Exceeded
```
❌ Query count (150) exceeds maximum allowed per run (100). 
   Please reduce your file to 100 queries or fewer.
```

## Bypassing Rate Limits

### Option 1: Use CSV Bypass
In Step 4 (Google Benchmark), check:
☑️ "Use pre-loaded Google results from CSV (bypass API calls)"

This uses cached results from `google_results.csv` instead of making API calls, so **no rate limits apply**.

### Option 2: Disable Rate Limiting
Set `RATE_LIMITING_ENABLED = False` in `services/rate_limiter.py`

**Warning**: Only disable if you're comfortable with potential API costs!

## Monitoring Usage

### View Current Status
```bash
curl http://localhost:8000/api/usage-status | python -m json.tool
```

### Check Usage File
```bash
cat usage_data.json
```

Example output:
```json
{
  "daily": {
    "queries": 150,
    "runs": 2,
    "date": "2026-03-02",
    "last_run_time": "2026-03-02T14:30:00"
  },
  "monthly": {
    "queries": 1250,
    "month": "2026-03"
  }
}
```

### Reset Usage Manually

To reset all counters:
```bash
rm usage_data.json
```

The file will be recreated automatically with zero counts.

## API Endpoint

### GET /api/usage-status

Returns current usage state:

```json
{
  "rate_limiting_enabled": true,
  "daily": {
    "used": 150,
    "limit": 500,
    "resets_at": "2026-03-03T00:00:00"
  },
  "monthly": {
    "used": 1250,
    "limit": 5000,
    "resets_at": "2026-04-01T00:00:00"
  },
  "runs": {
    "used_today": 2,
    "free_limit": 3,
    "cooldown_active": false,
    "cooldown_remaining_seconds": 0,
    "cooldown_ends_at": null
  },
  "max_queries_per_run": 100
}
```

## Troubleshooting

### Dashboard Not Showing
- **Cause**: Rate limiting is disabled
- **Solution**: Set `RATE_LIMITING_ENABLED = True` in `services/rate_limiter.py`

### Can't Run Analysis
- **Check 1**: Is cooldown active? Wait for the timer to expire
- **Check 2**: Have you hit daily/monthly limits? Check the dashboard
- **Check 3**: Does your file have more than 100 rows? Reduce the file size

### Usage Not Resetting
- **Daily**: Resets at midnight (server time)
- **Monthly**: Resets on the 1st of the month
- **Manual Reset**: Delete `usage_data.json`

### Cooldown Not Working
- Cooldown only applies after 3 runs in a day
- Make sure `usage_data.json` is not being deleted between runs
- Check that the server time is correct

## Best Practices

1. **Plan Your Runs**
   - You get 3 free runs per day
   - Each run can process up to 100 queries
   - That's 300 queries per day without cooldown

2. **Use CSV Bypass for Testing**
   - When testing the tool, use the CSV bypass option
   - Save your API quota for production runs

3. **Monitor Your Usage**
   - Check the dashboard regularly
   - Keep an eye on monthly usage
   - Set reminders before limits are reached

4. **Batch Your Queries**
   - Process up to 100 queries per run
   - Don't split into smaller runs unnecessarily
   - This maximizes your daily quota

5. **Respect Cooldowns**
   - The 3-hour cooldown prevents overuse
   - Use this time to analyze results
   - Plan your next batch of queries

## Cost Estimation

With the default limits:
- **Daily**: 500 queries × $5 per 1000 = $2.50/day max
- **Monthly**: 5000 queries × $5 per 1000 = $25/month max

Google Custom Search API pricing: https://developers.google.com/custom-search/v1/overview

## Support

If you need to adjust limits or have questions:
1. Review this guide
2. Check `IMPLEMENTATION_SUMMARY.md` for technical details
3. Examine `services/rate_limiter.py` for the implementation

---

**Your Google API Key**: AIzaSyBhV6ZF0S_pqD9JRkUi7iKDEy6VP7_guY0

**Remember**: This key is billing-enabled. The rate limiting system protects you from unexpected charges, but always monitor your usage!

