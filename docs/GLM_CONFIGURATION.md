# GLM model and token limits

Edit `/etc/sglang-npu-bot/bot.env` on the reporting host. Change existing entries
rather than appending duplicate keys. Existing installations retain this file
when updating the code.

## Select GLM-5.3

```ini
GLM_MODEL=glm-5.3
GLM_REASONING_EFFORT=high
GLM_ANALYSIS_MODE=auto
```

Keep the API key private. For a standard BigModel API account, use:

```ini
GLM_API_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

For an eligible GLM Coding Plan integration, use the plan's separate endpoint:

```ini
GLM_API_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
```

The bot sends OpenAI-compatible Chat Completions requests and enables thinking.
GLM-5.3 supports `low`, `high`, and `max` effort; thinking cannot be disabled.
See the official [model documentation](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)
and [Coding Plan model-switch guide](https://docs.bigmodel.cn/cn/coding-plan/latest-model).
Changing the model does not change provider entitlements or the bot's spending caps.
New runs use the edited environment; a running report keeps its current settings.

## Increase output tokens

These optional settings permit longer investigations and synthesis:

```ini
GLM_MAX_OUTPUT_TOKENS=32768
GLM_TRIAGE_MAX_TOKENS=12000
GLM_CLUSTER_MAX_TOKENS=24000
GLM_SYNTHESIS_MAX_TOKENS=28000
GLM_API_TIMEOUT_SECONDS=720
GLM_EMPTY_RETRIES=0
```

These are per-request output ceilings, including reasoning and the visible
answer. The bot currently accepts at most **32768**, even if the provider model
supports a larger output. Each stage is also bounded by `GLM_MAX_OUTPUT_TOKENS`.
Increasing only the global ceiling leaves the stage limits unchanged.
Manual refresh wrappers have their own `GLM_MANUAL_*` or `GLM_REFRESH_*`
overrides; the settings above control the normal scheduled analysis.

## Provider quota and local spending limits

Provider quota is managed in your BigModel account: upgrade the Coding Plan or
add the appropriate API balance/resource package for the endpoint you use.
Changing `max_tokens` does not replenish that quota. Coding Plan quota and
standard API balance are separate.

The bot independently enforces these local guards:

| Setting | Controls |
| --- | --- |
| `GLM_DAILY_BUDGET_CNY` | Maximum estimated spend per Moscow day |
| `GLM_PER_REQUEST_BUDGET_CNY` | Maximum reservation for one request |
| `GLM_MAX_DAILY_API_REQUESTS` | Maximum requests per Moscow day |
| `GLM_INPUT_CNY_PER_M` / `GLM_OUTPUT_CNY_PER_M` | Your model's actual contracted prices per million tokens |

Update the configured prices when changing models. The example prices shipped
with the kit are placeholders, not a GLM-5.3 tariff. Larger output reservations
can hit the existing spending limits before a request is sent. Review the
ledger and choose any higher spending caps yourself; no upgrade raises them.
Keep the ledger: deleting it does not restore provider quota and loses accounting.

To inspect local usage without a model call:

```bash
sudo systemd-run --wait --pipe --collect \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory=/home/sglang-npu-bot \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  /usr/bin/python3 scripts/glm-budget-status.py
```
