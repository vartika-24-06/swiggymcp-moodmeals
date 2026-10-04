# Spike notes

Outcomes of the Phase 0 spikes (tasks.md). No personal data belongs here: no
addresses, phone numbers, names or counts taken from an account.

## Spike A: Swiggy sign-in from Python (T0.2, design DQ1, requirements Q2)

- **Date:** 2026-10-04
- **Result:** SUCCESS
- **Environment:** owner's Windows laptop, `mcp` SDK 2.3.0 on Python 3.14.7
- **Script:** `spikes/swiggy_signin.py`

### What worked

- **Sign-in:** OAuth with dynamic client registration and PKCE, as a public
  client, with the redirect to `http://localhost:8765/callback`.
- **`list_tools`:** returned 20 tools: `get_addresses`, `create_address`,
  `delete_address`, `search_restaurants`, `search_menu`, `get_restaurant_menu`,
  `get_food_cart`, `update_food_cart`, `flush_food_cart`, `place_food_order`,
  `fetch_food_coupons`, `apply_food_coupon`, `get_food_orders`,
  `get_food_order_details`, `track_food_order`, `get_food_delivery_status`,
  `report_error`, `get_payment_options`, `check_payment_status`, `confirm_order`.
- **`get_addresses`:** returned in about 16 seconds, and `pagination.total` was
  readable in the result. It was the only tool called.

### Fallback

Not needed. The gate in tasks.md (another MCP client for sign-in) does not apply.

### Open points

- **Unexplained stall:** the first run signed in and then showed no output for
  over a minute at the `get_addresses` call. Progress lines and a 90-second
  limit were added afterwards and the next run finished normally. The cause is
  unknown.
- **Tool timeout:** about 16 seconds for one call is longer than the 15-second
  `tool_timeout_s` in the run budget (design.md section 10.1). That value needs
  revisiting before the real provider is built.
