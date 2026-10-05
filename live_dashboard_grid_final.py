# ============================================================
# DELTA BTCUSD FUTURES GRID BOT
# PART 1 OF 4
# SETTINGS + DELTA API + AUTHENTICATION
# ============================================================

import os
import time
import json
import hmac
import hashlib
import requests
import pandas as pd
import numpy as np
import streamlit as st


# ============================================================
# PAGE SETTINGS
# ============================================================

st.set_page_config(
    page_title="SANJAY RANA - DELTA FUTURES GRID",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# LIVE TRADING SAFETY
# ============================================================

# False = Testing / chart / calculation mode
# True  = Real Delta orders

LIVE_TRADING = False


# ============================================================
# DELTA EXCHANGE
# ============================================================

DELTA_BASE_URL = "https://api.india.delta.exchange"

BTCUSD_SYMBOL = "BTCUSD"
BTCUSD_PRODUCT_ID = 27

# Delta BTCUSD:
# 1 contract = 0.001 BTC

CONTRACT_SIZE_BTC = 0.001


# ============================================================
# GENERAL BOT SETTINGS
# ============================================================

TIMEFRAME = "1h"

POLL_SECONDS = 5

AUTO_REFRESH = True


# ============================================================
# SUPERTREND SETTINGS
# ============================================================

SUPERTREND_ATR_LENGTH = 10

SUPERTREND_MULTIPLIER = 2.0

SUPERTREND_SOURCE = "HL2"


# ============================================================
# MAIN ACCOUNT SETTINGS
# ============================================================

MAIN_DIRECTION_MODE = "SAME_AS_SUPERTREND"

MAIN_GRID_RANGE_POINTS = 4500.0

MAIN_GRID_QUANTITY = 15

MAIN_ORDER_QTY_BTC = 0.001

MAIN_LEVERAGE = 10


# ============================================================
# SUB ACCOUNT SETTINGS
# ============================================================

SUB_DIRECTION_MODE = "OPPOSITE_TO_SUPERTREND"

SUB_GRID_RANGE_POINTS = 4500.0

SUB_GRID_QUANTITY = 15

SUB_ORDER_QTY_BTC = 0.001

SUB_LEVERAGE = 10


# ============================================================
# API KEYS
# ============================================================
#
# Streamlit Cloud:
#
# MAIN_API_KEY
# MAIN_API_SECRET
#
# SUB_API_KEY
# SUB_API_SECRET
#
# इन्हें Streamlit Secrets में रखना है.
#
# Example:
#
# MAIN_API_KEY = "xxxxxxxx"
# MAIN_API_SECRET = "xxxxxxxx"
#
# SUB_API_KEY = "xxxxxxxx"
# SUB_API_SECRET = "xxxxxxxx"
#
# ============================================================


def get_secret(name, default=""):
    """
    पहले Streamlit Secrets से value लेता है.
    अगर वहाँ नहीं है तो environment variable से लेता है.
    """

    try:
        value = st.secrets.get(name, None)

        if value is not None:
            return str(value)

    except Exception:
        pass

    value = os.getenv(name, default)

    if value is None:
        return default

    return str(value)


MAIN_API_KEY = get_secret("MAIN_API_KEY")

MAIN_API_SECRET = get_secret("MAIN_API_SECRET")

SUB_API_KEY = get_secret("SUB_API_KEY")

SUB_API_SECRET = get_secret("SUB_API_SECRET")


# ============================================================
# GRID CALCULATION
# ============================================================

def calculate_grid_distance(range_points, grid_quantity):
    """
    Grid distance automatically calculate होगी.

    Example:

    Range = 4500
    Quantity = 15

    Distance = 4500 / 15
             = 300
    """

    if grid_quantity <= 0:
        return 0.0

    return float(range_points) / float(grid_quantity)


def btc_to_contracts(quantity_btc):
    """
    BTC quantity को Delta contracts में बदलता है.

    0.001 BTC = 1 contract
    """

    if quantity_btc <= 0:
        return 0

    contracts = quantity_btc / CONTRACT_SIZE_BTC

    return int(round(contracts))


MAIN_GRID_DISTANCE = calculate_grid_distance(
    MAIN_GRID_RANGE_POINTS,
    MAIN_GRID_QUANTITY,
)


SUB_GRID_DISTANCE = calculate_grid_distance(
    SUB_GRID_RANGE_POINTS,
    SUB_GRID_QUANTITY,
)


MAIN_ORDER_CONTRACTS = btc_to_contracts(
    MAIN_ORDER_QTY_BTC
)


SUB_ORDER_CONTRACTS = btc_to_contracts(
    SUB_ORDER_QTY_BTC
)


MAIN_TOTAL_BTC = (
    MAIN_GRID_QUANTITY *
    MAIN_ORDER_QTY_BTC
)


SUB_TOTAL_BTC = (
    SUB_GRID_QUANTITY *
    SUB_ORDER_QTY_BTC
)


MAIN_TOTAL_CONTRACTS = (
    MAIN_GRID_QUANTITY *
    MAIN_ORDER_CONTRACTS
)


SUB_TOTAL_CONTRACTS = (
    SUB_GRID_QUANTITY *
    SUB_ORDER_CONTRACTS
)


# ============================================================
# DELTA API SIGNATURE
# ============================================================

def delta_signature(
    api_secret,
    method,
    timestamp,
    request_path,
    query_string="",
    body=""
):
    """
    Delta Exchange authentication signature.

    Signature message:

    method
    timestamp
    request_path
    query_string
    body
    """

    message = (
        method.upper()
        + str(timestamp)
        + request_path
        + query_string
        + body
    )

    signature = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return signature


# ============================================================
# DELTA API CLIENT
# ============================================================

class DeltaClient:

    def __init__(
        self,
        api_key,
        api_secret,
        account_name="ACCOUNT"
    ):

        self.api_key = api_key

        self.api_secret = api_secret

        self.account_name = account_name

        self.base_url = DELTA_BASE_URL

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent":
                    "Sanjay-Rana-Delta-Grid-Bot/1.0"
            }
        )


    # ========================================================
    # PUBLIC REQUEST
    # ========================================================

    def public_request(
        self,
        method,
        path,
        params=None,
        body=None,
    ):

        url = self.base_url + path

        response = self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=body,
            timeout=15,
        )

        response.raise_for_status()

        return response.json()


    # ========================================================
    # PRIVATE REQUEST
    # ========================================================

    def private_request(
        self,
        method,
        path,
        params=None,
        body=None,
    ):

        if not self.api_key or not self.api_secret:

            raise ValueError(
                f"{self.account_name}: "
                "API key / secret is missing."
            )


        method = method.upper()


        # ----------------------------------------------------
        # Timestamp
        # ----------------------------------------------------

        timestamp = str(
            int(time.time())
        )


        # ----------------------------------------------------
        # Query string
        # ----------------------------------------------------

        query_string = ""

        if params:

            from urllib.parse import urlencode

            query_string = urlencode(
                sorted(params.items())
            )


        # ----------------------------------------------------
        # Request body
        # ----------------------------------------------------

        body_string = ""

        if body is not None:

            body_string = json.dumps(
                body,
                separators=(",", ":"),
            )


        # ----------------------------------------------------
        # Signature
        # ----------------------------------------------------

        signature = delta_signature(
            self.api_secret,
            method,
            timestamp,
            path,
            query_string,
            body_string,
        )


        # ----------------------------------------------------
        # Headers
        # ----------------------------------------------------

        headers = {

            "api-key":
                self.api_key,

            "signature":
                signature,

            "timestamp":
                timestamp,

            "User-Agent":
                "Sanjay-Rana-Delta-Grid-Bot/1.0",

            "Content-Type":
                "application/json",
        }


        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        url = self.base_url + path


        # ----------------------------------------------------
        # Request
        # ----------------------------------------------------

        response = self.session.request(

            method=method,

            url=url,

            params=params,

            data=body_string
                if body is not None
                else None,

            headers=headers,

            timeout=15,
        )


        # ----------------------------------------------------
        # Error handling
        # ----------------------------------------------------

        if not response.ok:

            raise RuntimeError(

                f"{self.account_name} "
                f"Delta API Error "
                f"{response.status_code}: "
                f"{response.text}"
            )


        return response.json()


    # ========================================================
    # TICKER
    # ========================================================

    def get_ticker(self):

        return self.public_request(

            "GET",

            "/v2/tickers/" +
            BTCUSD_SYMBOL,
        )


    # ========================================================
    # CANDLES
    # ========================================================

    def get_candles(
        self,
        resolution="1h",
        limit=500,
    ):

        params = {

            "symbol":
                BTCUSD_SYMBOL,

            "resolution":
                resolution,

            "limit":
                min(int(limit), 2000),
        }

        return self.public_request(

            "GET",

            "/v2/history/candles",

            params=params,
        )


    # ========================================================
    # POSITIONS
    # ========================================================

    def get_positions(self):

        return self.private_request(

            "GET",

            "/v2/positions",

            params={
                "product_id":
                    BTCUSD_PRODUCT_ID
            },
        )


    # ========================================================
    # OPEN ORDERS
    # ========================================================

    def get_open_orders(self):

        return self.private_request(

            "GET",

            "/v2/orders",

            params={
                "product_id":
                    BTCUSD_PRODUCT_ID,

                "page_size":
                    100,
            },
        )


    # ========================================================
    # PLACE ORDER
    # ========================================================

    def place_order(
        self,
        side,
        size,
        limit_price,
        client_order_id,
        reduce_only=False,
    ):

        order_side = str(side).lower()

        payload = {

            "product_id":
                BTCUSD_PRODUCT_ID,

            "order_type":
                "limit_order",

            "side":
                order_side,

            "size":
                int(size),

            "limit_price":
                str(limit_price),

            "client_order_id":
                str(client_order_id)[
                    :32
                ],

        }


        if reduce_only:

            payload["reduce_only"] = True


        return self.private_request(

            "POST",

            "/v2/orders",

            body=payload,
        )


# ============================================================
# ACCOUNT OBJECTS
# ============================================================

MAIN_CLIENT = DeltaClient(

    MAIN_API_KEY,

    MAIN_API_SECRET,

    "MAIN ACCOUNT",
)


SUB_CLIENT = DeltaClient(

    SUB_API_KEY,

    SUB_API_SECRET,

    "SUB ACCOUNT",
)


# ============================================================
# BASIC VALIDATION
# ============================================================

def validate_settings():

    errors = []


    if MAIN_GRID_RANGE_POINTS <= 0:

        errors.append(
            "MAIN_GRID_RANGE_POINTS must be > 0"
        )


    if SUB_GRID_RANGE_POINTS <= 0:

        errors.append(
            "SUB_GRID_RANGE_POINTS must be > 0"
        )


    if MAIN_GRID_QUANTITY <= 0:

        errors.append(
            "MAIN_GRID_QUANTITY must be > 0"
        )


    if SUB_GRID_QUANTITY <= 0:

        errors.append(
            "SUB_GRID_QUANTITY must be > 0"
        )


    if MAIN_ORDER_QTY_BTC <= 0:

        errors.append(
            "MAIN_ORDER_QTY_BTC must be > 0"
        )


    if SUB_ORDER_QTY_BTC <= 0:

        errors.append(
            "SUB_ORDER_QTY_BTC must be > 0"
        )


    if errors:

        raise ValueError(
            "\n".join(errors)
        )


validate_settings()


# ============================================================
# PART 1 STATUS
# ============================================================

PART_1_READY = True

# ============================================================
# DELTA BTCUSD FUTURES GRID BOT
# PART 2 OF 4
# CANDLES + SUPERTREND + SIGNAL + GRID CALCULATION
# ============================================================


# ============================================================
# CANDLE DATA NORMALIZATION
# ============================================================

def normalize_candles(raw_data):

    if raw_data is None:
        return pd.DataFrame()


    if isinstance(raw_data, dict):

        data = raw_data.get(
            "result",
            raw_data.get("data", [])
        )

    else:

        data = raw_data


    if not data:

        return pd.DataFrame()


    rows = []


    for item in data:

        if isinstance(item, dict):

            timestamp = (
                item.get("time")
                or item.get("timestamp")
                or item.get("start")
            )

            open_price = item.get("open")

            high_price = item.get("high")

            low_price = item.get("low")

            close_price = item.get("close")

            volume = (
                item.get("volume")
                or 0
            )


        else:

            if len(item) < 5:
                continue

            timestamp = item[0]

            open_price = item[1]

            high_price = item[2]

            low_price = item[3]

            close_price = item[4]

            volume = (
                item[5]
                if len(item) > 5
                else 0
            )


        try:

            rows.append(
                {
                    "time":
                        int(timestamp),

                    "open":
                        float(open_price),

                    "high":
                        float(high_price),

                    "low":
                        float(low_price),

                    "close":
                        float(close_price),

                    "volume":
                        float(volume),
                }
            )

        except Exception:

            continue


    if not rows:

        return pd.DataFrame()


    df = pd.DataFrame(rows)


    # --------------------------------------------------------
    # Timestamp conversion
    # --------------------------------------------------------

    if df["time"].max() > 10_000_000_000:

        df["datetime"] = pd.to_datetime(
            df["time"],
            unit="ms",
            utc=True,
        )

    else:

        df["datetime"] = pd.to_datetime(
            df["time"],
            unit="s",
            utc=True,
        )


    # --------------------------------------------------------
    # Sort oldest -> newest
    # --------------------------------------------------------

    df = df.sort_values(
        "datetime"
    ).reset_index(
        drop=True
    )


    # --------------------------------------------------------
    # Remove duplicate candles
    # --------------------------------------------------------

    df = df.drop_duplicates(
        subset=["datetime"],
        keep="last",
    ).reset_index(
        drop=True
    )


    return df


# ============================================================
# LOAD 1 HOUR CANDLES
# ============================================================

def load_btcusd_1h_candles(
    client,
    limit=500,
):

    try:

        raw = client.get_candles(
            resolution="1h",
            limit=limit,
        )

        df = normalize_candles(raw)

        return df

    except Exception as exc:

        st.error(
            f"{client.account_name} candle error: "
            f"{exc}"
        )

        return pd.DataFrame()


# ============================================================
# HL2 SOURCE
# ============================================================

def calculate_hl2(df):

    result = df.copy()

    result["hl2"] = (
        result["high"]
        +
        result["low"]
    ) / 2.0

    return result


# ============================================================
# ATR - WILDER / RMA
# ============================================================

def calculate_atr(
    df,
    length=10,
):

    result = df.copy()


    previous_close = (
        result["close"].shift(1)
    )


    tr1 = (
        result["high"]
        -
        result["low"]
    )


    tr2 = (
        result["high"]
        -
        previous_close
    ).abs()


    tr3 = (
        result["low"]
        -
        previous_close
    ).abs()


    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)


    # TradingView's ATR uses RMA.
    # Pandas ewm(alpha=1/length) gives
    # the Wilder/RMA style calculation.

    result["tr"] = true_range

    result["atr"] = (
        true_range
        .ewm(
            alpha=1.0 / float(length),
            adjust=False,
            min_periods=length,
        )
        .mean()
    )


    return result


# ============================================================
# SUPERTREND
# ============================================================

def calculate_supertrend(
    df,
    atr_length=10,
    multiplier=2.0,
):

    result = df.copy()


    if result.empty:

        return result


    # --------------------------------------------------------
    # HL2
    # --------------------------------------------------------

    result = calculate_hl2(
        result
    )


    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    result = calculate_atr(
        result,
        length=atr_length,
    )


    # --------------------------------------------------------
    # Basic Bands
    # --------------------------------------------------------

    result["basic_upper"] = (
        result["hl2"]
        +
        multiplier * result["atr"]
    )


    result["basic_lower"] = (
        result["hl2"]
        -
        multiplier * result["atr"]
    )


    # --------------------------------------------------------
    # Final Bands
    # --------------------------------------------------------

    final_upper = np.full(
        len(result),
        np.nan,
        dtype=float,
    )


    final_lower = np.full(
        len(result),
        np.nan,
        dtype=float,
    )


    supertrend = np.full(
        len(result),
        np.nan,
        dtype=float,
    )


    direction = np.full(
        len(result),
        np.nan,
        dtype=float,
    )


    close_values = (
        result["close"]
        .to_numpy(dtype=float)
    )


    basic_upper_values = (
        result["basic_upper"]
        .to_numpy(dtype=float)
    )


    basic_lower_values = (
        result["basic_lower"]
        .to_numpy(dtype=float)
    )


    atr_values = (
        result["atr"]
        .to_numpy(dtype=float)
    )


    # --------------------------------------------------------
    # Supertrend calculation
    # --------------------------------------------------------

    for i in range(len(result)):

        if np.isnan(
            atr_values[i]
        ):

            continue


        if i == 0:

            final_upper[i] = (
                basic_upper_values[i]
            )

            final_lower[i] = (
                basic_lower_values[i]
            )

            supertrend[i] = np.nan

            direction[i] = np.nan

            continue


        previous_upper = (
            final_upper[i - 1]
        )

        previous_lower = (
            final_lower[i - 1]
        )


        # ----------------------------------------------------
        # Final upper band
        # ----------------------------------------------------

        if (
            basic_upper_values[i]
            < previous_upper
            or
            close_values[i - 1]
            > previous_upper
        ):

            final_upper[i] = (
                basic_upper_values[i]
            )

        else:

            final_upper[i] = (
                previous_upper
            )


        # ----------------------------------------------------
        # Final lower band
        # ----------------------------------------------------

        if (
            basic_lower_values[i]
            > previous_lower
            or
            close_values[i - 1]
            < previous_lower
        ):

            final_lower[i] = (
                basic_lower_values[i]
            )

        else:

            final_lower[i] = (
                previous_lower
            )


        # ----------------------------------------------------
        # Direction / Supertrend
        # ----------------------------------------------------

        previous_supertrend = (
            supertrend[i - 1]
        )


        if np.isnan(
            previous_supertrend
        ):

            if (
                close_values[i]
                <= final_upper[i]
            ):

                supertrend[i] = (
                    final_upper[i]
                )

                direction[i] = 1.0

            else:

                supertrend[i] = (
                    final_lower[i]
                )

                direction[i] = -1.0

            continue


        if (
            previous_supertrend
            == final_upper[i - 1]
        ):

            if (
                close_values[i]
                <= final_upper[i]
            ):

                supertrend[i] = (
                    final_upper[i]
                )

                direction[i] = 1.0

            else:

                supertrend[i] = (
                    final_lower[i]
                )

                direction[i] = -1.0

        else:

            if (
                close_values[i]
                >= final_lower[i]
            ):

                supertrend[i] = (
                    final_lower[i]
                )

                direction[i] = -1.0

            else:

                supertrend[i] = (
                    final_upper[i]
                )

                direction[i] = 1.0


    result["supertrend"] = (
        supertrend
    )


    result["supertrend_direction"] = (
        direction
    )


    # --------------------------------------------------------
    # Direction labels
    # --------------------------------------------------------

    result["supertrend_signal"] = (
        np.where(
            result[
                "supertrend_direction"
            ] < 0,
            "BUY",
            "SELL",
        )
    )


    # --------------------------------------------------------
    # New signal detection
    # --------------------------------------------------------

    previous_direction = (
        result[
            "supertrend_direction"
        ].shift(1)
    )


    result["new_buy_signal"] = (
        (
            result[
                "supertrend_direction"
            ] < 0
        )
        &
        (
            previous_direction >= 0
        )
    )


    result["new_sell_signal"] = (
        (
            result[
                "supertrend_direction"
            ] > 0
        )
        &
        (
            previous_direction <= 0
        )
    )


    return result


# ============================================================
# CURRENT SUPERTREND STATE
# ============================================================

def get_current_supertrend_state(
    df,
):

    if df.empty:

        return None


    valid = df.dropna(
        subset=[
            "supertrend",
            "supertrend_direction",
        ]
    )


    if valid.empty:

        return None


    row = valid.iloc[-1]


    direction = (
        "BUY"
        if row[
            "supertrend_direction"
        ] < 0
        else "SELL"
    )


    return {

        "direction":
            direction,

        "supertrend":
            float(
                row["supertrend"]
            ),

        "candle_time":
            row["datetime"],

        "close":
            float(
                row["close"]
            ),

        "high":
            float(
                row["high"]
            ),

        "low":
            float(
                row["low"]
            ),

        "atr":
            float(
                row["atr"]
            ),
    }


# ============================================================
# FIND LAST NEW SUPERTREND SIGNAL
# ============================================================

def get_last_signal(
    df,
):

    if df.empty:

        return None


    buy_rows = df[
        df["new_buy_signal"]
    ]


    sell_rows = df[
        df["new_sell_signal"]
    ]


    candidates = []


    if not buy_rows.empty:

        candidates.append(
            buy_rows.iloc[-1]
        )


    if not sell_rows.empty:

        candidates.append(
            sell_rows.iloc[-1]
        )


    if not candidates:

        return None


    row = max(
        candidates,
        key=lambda x:
            x["datetime"]
    )


    if (
        row[
            "supertrend_direction"
        ] < 0
    ):

        signal = "BUY"

    else:

        signal = "SELL"


    return {

        "signal":
            signal,

        "signal_time":
            row["datetime"],

        "signal_close":
            float(
                row["close"]
            ),

        "signal_supertrend":
            float(
                row["supertrend"]
            ),

        "signal_atr":
            float(
                row["atr"]
            ),
    }


# ============================================================
# GRID PRICE GENERATOR
# ============================================================

def build_grid_prices(
    base_price,
    range_points,
    grid_quantity,
    direction,
):

    base_price = float(
        base_price
    )


    range_points = float(
        range_points
    )


    grid_quantity = int(
        grid_quantity
    )


    if grid_quantity <= 0:

        return []


    grid_distance = (
        range_points
        /
        grid_quantity
    )


    prices = []


    # --------------------------------------------------------
    # LONG GRID
    #
    # Base = Supertrend line
    # Grid moves upward.
    # --------------------------------------------------------

    if direction == "LONG":

        for i in range(
            grid_quantity + 1
        ):

            price = (
                base_price
                +
                i * grid_distance
            )

            prices.append(
                float(price)
            )


    # --------------------------------------------------------
    # SHORT GRID
    #
    # Base = Supertrend line
    # Grid moves downward.
    # --------------------------------------------------------

    elif direction == "SHORT":

        for i in range(
            grid_quantity + 1
        ):

            price = (
                base_price
                -
                i * grid_distance
            )

            prices.append(
                float(price)
            )


    return prices


# ============================================================
# GRID INFORMATION
# ============================================================

def get_grid_information(
    base_price,
    range_points,
    grid_quantity,
    direction,
    order_qty_btc,
):

    grid_distance = (
        calculate_grid_distance(
            range_points,
            grid_quantity,
        )
    )


    prices = build_grid_prices(
        base_price=base_price,
        range_points=range_points,
        grid_quantity=grid_quantity,
        direction=direction,
    )


    contracts = btc_to_contracts(
        order_qty_btc
    )


    total_btc = (
        grid_quantity
        *
        order_qty_btc
    )


    total_contracts = (
        grid_quantity
        *
        contracts
    )


    return {

        "base_price":
            float(base_price),

        "range_points":
            float(range_points),

        "grid_quantity":
            int(grid_quantity),

        "grid_distance":
            float(grid_distance),

        "order_qty_btc":
            float(order_qty_btc),

        "order_contracts":
            int(contracts),

        "total_btc":
            float(total_btc),

        "total_contracts":
            int(total_contracts),

        "direction":
            direction,

        "prices":
            prices,
    }


# ============================================================
# ACCOUNT GRID INFORMATION
# ============================================================

def build_account_grid_info(
    account_name,
    signal,
    supertrend_price,
):

    if account_name == "MAIN":

        range_points = (
            MAIN_GRID_RANGE_POINTS
        )

        grid_quantity = (
            MAIN_GRID_QUANTITY
        )

        order_qty_btc = (
            MAIN_ORDER_QTY_BTC
        )

        mode = (
            MAIN_DIRECTION_MODE
        )


    else:

        range_points = (
            SUB_GRID_RANGE_POINTS
        )

        grid_quantity = (
            SUB_GRID_QUANTITY
        )

        order_qty_btc = (
            SUB_ORDER_QTY_BTC
        )

        mode = (
            SUB_DIRECTION_MODE
        )


    # --------------------------------------------------------
    # MAIN = same as Supertrend
    # SUB = opposite
    # --------------------------------------------------------

    if signal == "BUY":

        if mode == "SAME_AS_SUPERTREND":

            account_direction = "LONG"

        else:

            account_direction = "SHORT"


    else:

        if mode == "SAME_AS_SUPERTREND":

            account_direction = "SHORT"

        else:

            account_direction = "LONG"


    return get_grid_information(

        base_price=supertrend_price,

        range_points=range_points,

        grid_quantity=grid_quantity,

        direction=account_direction,

        order_qty_btc=order_qty_btc,
    )


# ============================================================
# PROCESS SUPERTREND DATA
# ============================================================

def prepare_supertrend_data(
    client,
    candle_limit=500,
):

    df = load_btcusd_1h_candles(
        client,
        limit=candle_limit,
    )


    if df.empty:

        return df


    df = calculate_supertrend(

        df,

        atr_length=
            SUPERTREND_ATR_LENGTH,

        multiplier=
            SUPERTREND_MULTIPLIER,
    )


    return df


# ============================================================
# MAIN + SUB DATA
# ============================================================

def prepare_market_data():

    # --------------------------------------------------------
    # Same BTCUSD 1H market data is used by both accounts.
    #
    # Account direction/grid/order state remains separate.
    # --------------------------------------------------------

    df = prepare_supertrend_data(
        MAIN_CLIENT,
        candle_limit=500,
    )


    if df.empty:

        return {

            "data":
                pd.DataFrame(),

            "state":
                None,

            "last_signal":
                None,
        }


    state = (
        get_current_supertrend_state(
            df
        )
    )


    last_signal = (
        get_last_signal(
            df
        )
    )


    return {

        "data":
            df,

        "state":
            state,

        "last_signal":
            last_signal,
    }


# ============================================================
# SIGNAL SNAPSHOT
# ============================================================

def create_signal_snapshot(
    market_data,
):

    state = (
        market_data.get(
            "state"
        )
    )


    last_signal = (
        market_data.get(
            "last_signal"
        )
    )


    if state is None:

        return None


    if last_signal is None:

        signal = state[
            "direction"
        ]

        signal_time = (
            state["candle_time"]
        )

        signal_price = (
            state["supertrend"]
        )

    else:

        signal = last_signal[
            "signal"
        ]

        signal_time = (
            last_signal[
                "signal_time"
            ]
        )

        signal_price = (
            last_signal[
                "signal_supertrend"
            ]
        )


    return {

        "signal":
            signal,

        "signal_time":
            signal_time,

        "supertrend_price":
            float(signal_price),

        "current_candle_close":
            float(state["close"]),

        "current_atr":
            float(state["atr"]),
    }


# ============================================================
# BUILD BOTH ACCOUNT GRID SNAPSHOTS
# ============================================================

def build_dual_grid_snapshot(
    market_data,
):

    snapshot = create_signal_snapshot(
        market_data
    )


    if snapshot is None:

        return None


    signal = snapshot[
        "signal"
    ]


    supertrend_price = (
        snapshot[
            "supertrend_price"
        ]
    )


    main_grid = (
        build_account_grid_info(

            account_name="MAIN",

            signal=signal,

            supertrend_price=
                supertrend_price,
        )
    )


    sub_grid = (
        build_account_grid_info(

            account_name="SUB",

            signal=signal,

            supertrend_price=
                supertrend_price,
        )
    )


    return {

        "signal":
            signal,

        "signal_time":
            snapshot[
                "signal_time"
            ],

        "supertrend_price":
            supertrend_price,

        "main":
            main_grid,

        "sub":
            sub_grid,
    }


# ============================================================
# PART 2 READY
# ============================================================

PART_2_READY = True

# ============================================================
# DELTA BTCUSD FUTURES GRID BOT
# PART 3 OF 4
# MAIN/SUB GRID ENGINE + ORDER + POSITION LOGIC
# ============================================================


# ============================================================
# GRID ENGINE
# ============================================================

class GridEngine:

    def __init__(
        self,
        account_name,
        direction_mode,
        grid_range_points,
        grid_quantity,
        order_qty_btc,
        leverage,
    ):

        self.account_name = (
            account_name
        )

        self.direction_mode = (
            direction_mode
        )

        self.grid_range_points = (
            float(grid_range_points)
        )

        self.grid_quantity = (
            int(grid_quantity)
        )

        self.order_qty_btc = (
            float(order_qty_btc)
        )

        self.leverage = (
            int(leverage)
        )


        # ----------------------------------------------------
        # Runtime state
        # ----------------------------------------------------

        self.signal = None

        self.signal_time = None

        self.base_price = None

        self.direction = None

        self.grid_distance = 0.0

        self.grid_prices = []

        self.cycle_id = None


        # ----------------------------------------------------
        # Order state
        # ----------------------------------------------------

        self.orders = {}

        self.filled_orders = {}

        self.targets = {}

        self.position_size = 0

        self.position_entry = None

        self.realized_pnl = 0.0

        self.unrealized_pnl = 0.0


        # ----------------------------------------------------
        # Shift state
        # ----------------------------------------------------

        self.shift_count = 0

        self.last_shift_price = None

        self.last_update_time = None


    # ========================================================
    # ACCOUNT DIRECTION
    # ========================================================

    def calculate_direction(
        self,
        supertrend_signal,
    ):

        if (
            self.direction_mode
            == "SAME_AS_SUPERTREND"
        ):

            if supertrend_signal == "BUY":

                return "LONG"

            return "SHORT"


        if (
            self.direction_mode
            == "OPPOSITE_TO_SUPERTREND"
        ):

            if supertrend_signal == "BUY":

                return "SHORT"

            return "LONG"


        raise ValueError(
            f"Unknown direction mode: "
            f"{self.direction_mode}"
        )


    # ========================================================
    # START NEW CYCLE
    # ========================================================

    def start_new_cycle(
        self,
        signal,
        signal_time,
        supertrend_price,
    ):

        self.signal = signal

        self.signal_time = (
            signal_time
        )

        self.base_price = float(
            supertrend_price
        )

        self.direction = (
            self.calculate_direction(
                signal
            )
        )


        self.grid_distance = (
            self.grid_range_points
            /
            self.grid_quantity
        )


        self.grid_prices = (
            build_grid_prices(

                base_price=
                    self.base_price,

                range_points=
                    self.grid_range_points,

                grid_quantity=
                    self.grid_quantity,

                direction=
                    self.direction,
            )
        )


        self.cycle_id = (
            f"{self.account_name}-"
            f"{int(time.time())}"
        )


        self.orders = {}

        self.filled_orders = {}

        self.targets = {}

        self.position_size = 0

        self.position_entry = None

        self.realized_pnl = 0.0

        self.unrealized_pnl = 0.0

        self.shift_count = 0

        self.last_shift_price = (
            self.base_price
        )

        self.last_update_time = (
            time.time()
        )


    # ========================================================
    # CURRENT RANGE
    # ========================================================

    def get_range(self):

        if not self.grid_prices:

            return None, None


        return (
            min(self.grid_prices),
            max(self.grid_prices),
        )


    # ========================================================
    # CREATE GRID LEVELS
    # ========================================================

    def create_grid_levels(
        self,
        current_price,
    ):

        if self.direction is None:

            return []


        levels = []


        for index, price in enumerate(
            self.grid_prices
        ):

            price = float(price)


            # ------------------------------------------------
            # LONG
            #
            # Lower levels = BUY entries
            # Higher levels = SELL targets
            # ------------------------------------------------

            if self.direction == "LONG":

                if price < current_price:

                    order_side = "buy"

                    level_type = "ENTRY"

                elif price > current_price:

                    order_side = "sell"

                    level_type = "TARGET"

                else:

                    order_side = "buy"

                    level_type = "ENTRY"


            # ------------------------------------------------
            # SHORT
            #
            # Higher levels = SELL entries
            # Lower levels = BUY targets
            # ------------------------------------------------

            else:

                if price > current_price:

                    order_side = "sell"

                    level_type = "ENTRY"

                elif price < current_price:

                    order_side = "buy"

                    level_type = "TARGET"

                else:

                    order_side = "sell"

                    level_type = "ENTRY"


            levels.append(

                {

                    "index":
                        index,

                    "price":
                        price,

                    "side":
                        order_side,

                    "type":
                        level_type,

                    "qty_btc":
                        self.order_qty_btc,

                    "contracts":
                        btc_to_contracts(
                            self.order_qty_btc
                        ),

                    "status":
                        "PENDING",
                }
            )


        return levels


    # ========================================================
    # ORDER CLIENT ID
    # ========================================================

    def make_client_order_id(
        self,
        level_index,
        side,
        purpose="GRID",
    ):

        cycle = str(
            self.cycle_id or "CYCLE"
        )


        cycle = (
            cycle
            .replace("-", "")
            .replace("_", "")
        )


        account = (
            self.account_name[:4]
        )


        side_code = (
            str(side).upper()[:1]
        )


        purpose_code = (
            str(purpose).upper()[:3]
        )


        raw = (
            f"SR{account}"
            f"{cycle[-8:]}"
            f"{purpose_code}"
            f"{side_code}"
            f"{level_index}"
        )


        return raw[:32]


    # ========================================================
    # REGISTER PENDING ORDERS
    # ========================================================

    def register_grid_orders(
        self,
        current_price,
    ):

        levels = (
            self.create_grid_levels(
                current_price
            )
        )


        self.orders = {}


        for level in levels:

            index = level[
                "index"
            ]


            order_key = (
                f"{self.cycle_id}-"
                f"{index}"
            )


            level[
                "client_order_id"
            ] = (
                self.make_client_order_id(
                    index,
                    level["side"],
                    "GRID",
                )
            )


            self.orders[
                order_key
            ] = level


        return list(
            self.orders.values()
        )


    # ========================================================
    # FIND LEVEL BY PRICE
    # ========================================================

    def find_level(
        self,
        price,
        tolerance=None,
    ):

        if not self.grid_prices:

            return None


        if tolerance is None:

            tolerance = (
                max(
                    self.grid_distance
                    * 0.10,
                    0.5,
                )
            )


        closest = None

        closest_distance = None


        for index, level_price in enumerate(
            self.grid_prices
        ):

            distance = abs(
                float(level_price)
                -
                float(price)
            )


            if (
                closest_distance is None
                or
                distance < closest_distance
            ):

                closest_distance = (
                    distance
                )

                closest = index


        if (
            closest_distance is not None
            and
            closest_distance <= tolerance
        ):

            return closest


        return None


    # ========================================================
    # MARK ORDER FILLED
    # ========================================================

    def mark_order_filled(
        self,
        order_key,
        fill_price,
        fill_qty_btc=None,
    ):

        if order_key not in self.orders:

            return False


        order = self.orders[
            order_key
        ]


        if fill_qty_btc is None:

            fill_qty_btc = (
                order["qty_btc"]
            )


        order["status"] = (
            "FILLED"
        )

        order["fill_price"] = (
            float(fill_price)
        )

        order["filled_qty_btc"] = (
            float(fill_qty_btc)
        )


        self.filled_orders[
            order_key
        ] = order


        # ----------------------------------------------------
        # Update position
        # ----------------------------------------------------

        signed_qty = (
            float(fill_qty_btc)
        )


        if order["side"] == "sell":

            signed_qty = (
                -signed_qty
            )


        self.position_size += (
            signed_qty
        )


        self.position_entry = (
            float(fill_price)
        )


        # ----------------------------------------------------
        # Create opposite target
        # ----------------------------------------------------

        target_side = (
            "sell"
            if order["side"] == "buy"
            else "buy"
        )


        level_index = order[
            "index"
        ]


        if self.direction == "LONG":

            if order["side"] == "buy":

                target_price = (
                    float(fill_price)
                    +
                    self.grid_distance
                )

            else:

                target_price = (
                    float(fill_price)
                    -
                    self.grid_distance
                )


        else:

            if order["side"] == "sell":

                target_price = (
                    float(fill_price)
                    -
                    self.grid_distance
                )

            else:

                target_price = (
                    float(fill_price)
                    +
                    self.grid_distance
                )


        target_id = (
            self.make_client_order_id(
                level_index,
                target_side,
                "TGT",
            )
        )


        self.targets[
            order_key
        ] = {

            "parent_order":
                order_key,

            "side":
                target_side,

            "price":
                float(target_price),

            "qty_btc":
                float(fill_qty_btc),

            "contracts":
                btc_to_contracts(
                    fill_qty_btc
                ),

            "client_order_id":
                target_id,

            "status":
                "PENDING",
        }


        return True


    # ========================================================
    # MARK TARGET HIT
    # ========================================================

    def mark_target_hit(
        self,
        parent_order_key,
        exit_price,
    ):

        target = self.targets.get(
            parent_order_key
        )


        if target is None:

            return False


        if target["status"] == "FILLED":

            return False


        target["status"] = (
            "FILLED"
        )

        target["fill_price"] = (
            float(exit_price)
        )


        # ----------------------------------------------------
        # Find parent
        # ----------------------------------------------------

        parent = self.filled_orders.get(
            parent_order_key
        )


        if parent is not None:

            entry_price = float(
                parent["fill_price"]
            )

            qty = float(
                parent["filled_qty_btc"]
            )


            if parent["side"] == "buy":

                pnl = (
                    float(exit_price)
                    -
                    entry_price
                ) * qty

            else:

                pnl = (
                    entry_price
                    -
                    float(exit_price)
                ) * qty


            self.realized_pnl += (
                pnl
            )


        # ----------------------------------------------------
        # Target cycle completed
        # ----------------------------------------------------

        return True


    # ========================================================
    # CURRENT TARGETS
    # ========================================================

    def get_pending_targets(self):

        return [

            target

            for target in self.targets.values()

            if target.get("status")
            == "PENDING"

        ]


    # ========================================================
    # CURRENT PENDING ENTRIES
    # ========================================================

    def get_pending_entries(self):

        return [

            order

            for order in self.orders.values()

            if (
                order.get("status")
                == "PENDING"
                and
                order.get("type")
                == "ENTRY"
            )

        ]


    # ========================================================
    # GRID SHIFT REQUIRED?
    # ========================================================

    def needs_grid_shift(
        self,
        current_price,
    ):

        if not self.grid_prices:

            return False


        lower, upper = (
            self.get_range()
        )


        current_price = float(
            current_price
        )


        # ----------------------------------------------------
        # Price outside range
        # ----------------------------------------------------

        if (
            current_price < lower
            or
            current_price > upper
        ):

            return True


        # ----------------------------------------------------
        # Move by one full grid distance
        # ----------------------------------------------------

        if self.last_shift_price is not None:

            movement = abs(
                current_price
                -
                self.last_shift_price
            )


            if movement >= (
                self.grid_distance
            ):

                return True


        return False


    # ========================================================
    # SHIFT GRID
    # ========================================================

    def shift_grid(
        self,
        current_price,
    ):

        current_price = float(
            current_price
        )


        # ----------------------------------------------------
        # Keep same grid spacing.
        #
        # Shift the grid window around the current market
        # while maintaining the original Supertrend cycle.
        # ----------------------------------------------------

        if self.direction == "LONG":

            new_base = (
                current_price
                -
                (
                    self.grid_distance
                    *
                    max(
                        1,
                        self.grid_quantity // 3
                    )
                )
            )


        else:

            new_base = (
                current_price
                +
                (
                    self.grid_distance
                    *
                    max(
                        1,
                        self.grid_quantity // 3
                    )
                )
            )


        self.base_price = (
            float(new_base)
        )


        self.grid_prices = (
            build_grid_prices(

                base_price=
                    self.base_price,

                range_points=
                    self.grid_range_points,

                grid_quantity=
                    self.grid_quantity,

                direction=
                    self.direction,
            )
        )


        self.shift_count += 1

        self.last_shift_price = (
            current_price
        )

        self.last_update_time = (
            time.time()
        )


        # ----------------------------------------------------
        # New pending grid list
        # ----------------------------------------------------

        self.orders = {}


        return self.grid_prices


    # ========================================================
    # UPDATE UNREALIZED PNL
    # ========================================================

    def update_unrealized_pnl(
        self,
        current_price,
    ):

        if (
            self.position_size == 0
            or
            self.position_entry is None
        ):

            self.unrealized_pnl = 0.0

            return self.unrealized_pnl


        qty = abs(
            float(
                self.position_size
            )
        )


        entry = float(
            self.position_entry
        )


        current = float(
            current_price
        )


        if self.position_size > 0:

            self.unrealized_pnl = (
                current
                -
                entry
            ) * qty

        else:

            self.unrealized_pnl = (
                entry
                -
                current
            ) * qty


        return self.unrealized_pnl


       # ========================================================
    # SNAPSHOT
    # ========================================================

    def snapshot(
        self,
        current_price=None,
    ):

        if current_price is not None:

            self.update_unrealized_pnl(
                current_price
            )


        lower, upper = (
            self.get_range()
        )


        return {

            "account":
                self.account_name,

            "signal":
                self.signal,

            "signal_time":
                self.signal_time,

            "direction":
                self.direction,

            "base_price":
                self.base_price,

            "grid_range":
                self.grid_range_points,

            "grid_quantity":
                self.grid_quantity,

            "grid_distance":
                self.grid_distance,

            "lower_price":
                lower,

            "upper_price":
                upper,

            "order_qty_btc":
                self.order_qty_btc,

            "order_contracts":
                btc_to_contracts(
                    self.order_qty_btc
                ),

            "position_size":
                self.position_size,

            "position_entry":
                self.position_entry,

            "realized_pnl":
                self.realized_pnl,

            "unrealized_pnl":
                self.unrealized_pnl,

            "shift_count":
                self.shift_count,

            "pending_entries":
                len(
                    self.get_pending_entries()
                ),

            "pending_targets":
                len(
                    self.get_pending_targets()
                ),

            "total_orders":
                len(
                    self.orders
                ),

            "total_filled":
                len(
                    self.filled_orders
                ),

            "cycle_id":
                self.cycle_id,
        }


# ============================================================
# CREATE MAIN ENGINE
# ============================================================

MAIN_ENGINE = GridEngine(

    account_name="MAIN",

    direction_mode=
        MAIN_DIRECTION_MODE,

    grid_range_points=
        MAIN_GRID_RANGE_POINTS,

    grid_quantity=
        MAIN_GRID_QUANTITY,

    order_qty_btc=
        MAIN_ORDER_QTY_BTC,

    leverage=
        MAIN_LEVERAGE,
)


# ============================================================
# CREATE SUB ENGINE
# ============================================================

SUB_ENGINE = GridEngine(

    account_name="SUB",

    direction_mode=
        SUB_DIRECTION_MODE,

    grid_range_points=
        SUB_GRID_RANGE_POINTS,

    grid_quantity=
        SUB_GRID_QUANTITY,

    order_qty_btc=
        SUB_ORDER_QTY_BTC,

    leverage=
        SUB_LEVERAGE,
)


# ============================================================
# START / RESET ENGINES WHEN SUPERTREND CHANGES
# ============================================================

def update_engines_from_signal(
    market_data,
):

    snapshot = (
        create_signal_snapshot(
            market_data
        )
    )


    if snapshot is None:

        return None


    signal = snapshot[
        "signal"
    ]


    signal_time = snapshot[
        "signal_time"
    ]


    supertrend_price = (
        snapshot[
            "supertrend_price"
        ]
    )


    # --------------------------------------------------------
    # MAIN
    # --------------------------------------------------------

    main_needs_new_cycle = (

        MAIN_ENGINE.cycle_id
        is None

        or

        MAIN_ENGINE.signal != signal

    )


    if main_needs_new_cycle:

        MAIN_ENGINE.start_new_cycle(

            signal=signal,

            signal_time=signal_time,

            supertrend_price=
                supertrend_price,
        )


    # --------------------------------------------------------
    # SUB
    # --------------------------------------------------------

    sub_needs_new_cycle = (

        SUB_ENGINE.cycle_id
        is None

        or

        SUB_ENGINE.signal != signal

    )


    if sub_needs_new_cycle:

        SUB_ENGINE.start_new_cycle(

            signal=signal,

            signal_time=signal_time,

            supertrend_price=
                supertrend_price,
        )


    return {

        "signal":
            signal,

        "signal_time":
            signal_time,

        "supertrend_price":
            supertrend_price,
    }


# ============================================================
# PREPARE ENGINE ORDERS
# ============================================================

def prepare_engine_orders(
    engine,
    current_price,
):

    if engine.cycle_id is None:

        return []


    # --------------------------------------------------------
    # First grid creation
    # --------------------------------------------------------

    if not engine.orders:

        return engine.register_grid_orders(
            current_price
        )


    # --------------------------------------------------------
    # Pionex-style shifting
    # --------------------------------------------------------

    if engine.needs_grid_shift(
        current_price
    ):

        engine.shift_grid(
            current_price
        )

        return engine.register_grid_orders(
            current_price
        )


    return list(
        engine.orders.values()
    )


# ============================================================
# DELTA ORDER STATUS HELPERS
# ============================================================

def extract_order_list(
    response,
):

    if response is None:

        return []


    if isinstance(
        response,
        list
    ):

        return response


    if isinstance(
        response,
        dict
    ):

        result = response.get(
            "result"
        )


        if isinstance(
            result,
            list
        ):

            return result


        if isinstance(
            result,
            dict
        ):

            for key in [
                "orders",
                "data",
                "result",
            ]:

                value = result.get(
                    key
                )


                if isinstance(
                    value,
                    list
                ):

                    return value


        for key in [
            "orders",
            "data",
        ]:

            value = response.get(
                key
            )


            if isinstance(
                value,
                list
            ):

                return value


    return []


# ============================================================
# MATCH DELTA ORDERS TO ENGINE
# ============================================================

def sync_engine_orders(
    engine,
    client,
):

    try:

        response = (
            client.get_open_orders()
        )

    except Exception:

        return []


    delta_orders = (
        extract_order_list(
            response
        )
    )


    return delta_orders


# ============================================================
# SEND NEW GRID ORDERS
# ============================================================

def send_grid_orders(
    engine,
    client,
    current_price,
):

    orders = prepare_engine_orders(

        engine,

        current_price,
    )


    results = []


    for order in orders:

        if order.get(
            "status"
        ) != "PENDING":

            continue


        # ----------------------------------------------------
        # Do not automatically place every target here.
        #
        # Targets are created only after an entry fill.
        # ----------------------------------------------------

        if order.get(
            "type"
        ) != "ENTRY":

            continue


        price = float(
            order["price"]
        )


        side = str(
            order["side"]
        ).lower()


        contracts = int(
            order["contracts"]
        )


        client_order_id = (
            order[
                "client_order_id"
            ]
        )


        # ----------------------------------------------------
        # TEST MODE
        # ----------------------------------------------------

        if not LIVE_TRADING:

            results.append(

                {

                    "mode":
                        "TEST",

                    "account":
                        engine.account_name,

                    "side":
                        side,

                    "price":
                        price,

                    "contracts":
                        contracts,

                    "client_order_id":
                        client_order_id,

                    "status":
                        "SIMULATED",
                }
            )

            continue


        # ----------------------------------------------------
        # REAL DELTA ORDER
        # ----------------------------------------------------

        try:

            response = (
                client.place_order(

                    side=side,

                    size=contracts,

                    limit_price=price,

                    client_order_id=
                        client_order_id,

                    reduce_only=False,
                )
            )


            order["delta_response"] = (
                response
            )


            order["submitted"] = (
                True
            )


            results.append(

                {

                    "mode":
                        "LIVE",

                    "account":
                        engine.account_name,

                    "side":
                        side,

                    "price":
                        price,

                    "contracts":
                        contracts,

                    "client_order_id":
                        client_order_id,

                    "status":
                        "SUBMITTED",

                    "response":
                        response,
                }
            )


        except Exception as exc:

            order["submitted"] = (
                False
            )

            order["error"] = (
                str(exc)
            )


            results.append(

                {

                    "mode":
                        "LIVE",

                    "account":
                        engine.account_name,

                    "side":
                        side,

                    "price":
                        price,

                    "contracts":
                        contracts,

                    "client_order_id":
                        client_order_id,

                    "status":
                        "ERROR",

                    "error":
                        str(exc),
                }
            )


    return results


# ============================================================
# CREATE TARGET ORDER AFTER FILL
# ============================================================

def submit_target_order(
    engine,
    client,
    parent_order_key,
):

    target = engine.targets.get(
        parent_order_key
    )


    if target is None:

        return None


    if target.get(
        "status"
    ) != "PENDING":

        return None


    if not LIVE_TRADING:

        target[
            "simulated"
        ] = True

        return {

            "mode":
                "TEST",

            "account":
                engine.account_name,

            "side":
                target["side"],

            "price":
                target["price"],

            "contracts":
                target["contracts"],

            "status":
                "SIMULATED",
        }


    try:

        response = (
            client.place_order(

                side=
                    target["side"],

                size=
                    target["contracts"],

                limit_price=
                    target["price"],

                client_order_id=
                    target[
                        "client_order_id"
                    ],

                reduce_only=True,
            )
        )


        target[
            "delta_response"
        ] = response


        target[
            "submitted"
        ] = True


        return {

            "mode":
                "LIVE",

            "account":
                engine.account_name,

            "side":
                target["side"],

            "price":
                target["price"],

            "contracts":
                target["contracts"],

            "status":
                "SUBMITTED",

            "response":
                response,
        }


    except Exception as exc:

        target[
            "submitted"
        ] = False


        target[
            "error"
        ] = str(exc)


        return {

            "mode":
                "LIVE",

            "account":
                engine.account_name,

            "side":
                target["side"],

            "price":
                target["price"],

            "contracts":
                target["contracts"],

            "status":
                "ERROR",

            "error":
                str(exc),
        }


# ============================================================
# ACCOUNT STATE
# ============================================================

def get_account_exchange_state(
    client,
):

    state = {

        "positions":
            [],

        "orders":
            [],

        "position_error":
            None,

        "order_error":
            None,
    }


    # --------------------------------------------------------
    # Positions
    # --------------------------------------------------------

    try:

        response = (
            client.get_positions()
        )

        state[
            "positions"
        ] = response

    except Exception as exc:

        state[
            "position_error"
        ] = str(exc)


    # --------------------------------------------------------
    # Open orders
    # --------------------------------------------------------

    try:

        response = (
            client.get_open_orders()
        )

        state[
            "orders"
        ] = response

    except Exception as exc:

        state[
            "order_error"
        ] = str(exc)


    return state


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_btcusd_price():

    try:

        response = (
            MAIN_CLIENT.get_ticker()
        )


        result = response.get(
            "result",
            response,
        )


        if isinstance(
            result,
            list
        ):

            if not result:

                return None

            result = result[0]


        for key in [
            "close",
            "last_price",
            "mark_price",
            "spot_price",
        ]:

            value = result.get(
                key
            )


            if value is not None:

                return float(value)


    except Exception:

        pass


    return None


# ============================================================
# UPDATE ENGINES
# ============================================================

def update_grid_engines():

    market_data = (
        prepare_market_data()
    )


    if market_data[
        "data"
    ].empty:

        return {

            "market":
                market_data,

            "signal":
                None,

            "price":
                None,

            "main":
                None,

            "sub":
                None,

            "main_orders":
                [],

            "sub_orders":
                [],
        }


    signal_info = (
        update_engines_from_signal(
            market_data
        )
    )


    current_price = (
        get_current_btcusd_price()
    )


    if current_price is None:

        current_price = float(
            market_data[
                "data"
            ].iloc[-1]["close"]
        )


    MAIN_ENGINE.update_unrealized_pnl(
        current_price
    )


    SUB_ENGINE.update_unrealized_pnl(
        current_price
    )


    main_orders = (
        send_grid_orders(

            MAIN_ENGINE,

            MAIN_CLIENT,

            current_price,
        )
    )


    sub_orders = (
        send_grid_orders(

            SUB_ENGINE,

            SUB_CLIENT,

            current_price,
        )
    )


    return {

        "market":
            market_data,

        "signal":
            signal_info,

        "price":
            current_price,

        "main":
            MAIN_ENGINE.snapshot(
                current_price
            ),

        "sub":
            SUB_ENGINE.snapshot(
                current_price
            ),

        "main_orders":
            main_orders,

        "sub_orders":
            sub_orders,
    }


# ============================================================
# PART 3 READY
# ============================================================

PART_3_READY = True

# ============================================================
# DELTA BTCUSD FUTURES GRID BOT
# PART 4 OF 4
# DASHBOARD + TWO SEPARATE CHARTS + AUTO REFRESH
# ============================================================

import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ============================================================
# DASHBOARD CSS
# ============================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 34px;
        font-weight: 800;
        text-align: center;
        margin-bottom: 5px;
    }

    .sub-title {
        font-size: 18px;
        font-weight: 700;
        text-align: center;
        margin-bottom: 20px;
    }

    .signal-buy {
        font-size: 32px;
        font-weight: 900;
        text-align: center;
        padding: 12px;
        border-radius: 10px;
        background: #123d20;
        color: #00ff66;
    }

    .signal-sell {
        font-size: 32px;
        font-weight: 900;
        text-align: center;
        padding: 12px;
        border-radius: 10px;
        background: #4a1111;
        color: #ff3333;
    }

    .info-box {
        padding: 12px;
        border-radius: 10px;
        background: #151515;
        margin-bottom: 8px;
    }

    .big-number {
        font-size: 24px;
        font-weight: 800;
    }

    .small-label {
        font-size: 13px;
        opacity: 0.75;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# TITLE
# ============================================================

st.markdown(
    '<div class="main-title">'
    'SANJAY RANA — DELTA FUTURES GRID BOT'
    '</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    'BTCUSD • 1 HOUR • SUPERTREND 10,2 • HL2'
    '</div>',
    unsafe_allow_html=True,
)


# ============================================================
# LOAD BOT DATA
# ============================================================

try:

    BOT_DATA = update_grid_engines()

except Exception as exc:

    st.error(
        f"Bot update error: {exc}"
    )

    BOT_DATA = {

        "market": {
            "data":
                pd.DataFrame(),

            "state":
                None,

            "last_signal":
                None,
        },

        "signal":
            None,

        "price":
            None,

        "main":
            None,

        "sub":
            None,

        "main_orders":
            [],

        "sub_orders":
            [],
    }


# ============================================================
# MARKET DATA
# ============================================================

MARKET_DATA = BOT_DATA.get(
    "market",
    {},
)

CANDLE_DF = MARKET_DATA.get(
    "data",
    pd.DataFrame(),
)

CURRENT_PRICE = BOT_DATA.get(
    "price"
)


SIGNAL_INFO = BOT_DATA.get(
    "signal"
)


if SIGNAL_INFO:

    CURRENT_SIGNAL = SIGNAL_INFO.get(
        "signal"
    )

    SIGNAL_TIME = SIGNAL_INFO.get(
        "signal_time"
    )

    SUPERTREND_PRICE = SIGNAL_INFO.get(
        "supertrend_price"
    )

else:

    state = MARKET_DATA.get(
        "state"
    )


    if state:

        CURRENT_SIGNAL = state.get(
            "direction"
        )

        SIGNAL_TIME = state.get(
            "candle_time"
        )

        SUPERTREND_PRICE = state.get(
            "supertrend"
        )

    else:

        CURRENT_SIGNAL = None

        SIGNAL_TIME = None

        SUPERTREND_PRICE = None


# ============================================================
# SIGNAL HEADER
# ============================================================

if CURRENT_SIGNAL == "BUY":

    st.markdown(
        '<div class="signal-buy">'
        '🟢 CURRENT SUPERTREND: BUY'
        '</div>',
        unsafe_allow_html=True,
    )

elif CURRENT_SIGNAL == "SELL":

    st.markdown(
        '<div class="signal-sell">'
        '🔴 CURRENT SUPERTREND: SELL'
        '</div>',
        unsafe_allow_html=True,
    )

else:

    st.info(
        "Supertrend signal available नहीं है."
    )


# ============================================================
# SIGNAL INFORMATION
# ============================================================

signal_col1, signal_col2, signal_col3 = (
    st.columns(3)
)


with signal_col1:

    st.metric(
        "CURRENT BTCUSD",
        (
            f"{CURRENT_PRICE:,.2f}"
            if CURRENT_PRICE is not None
            else "-"
        ),
    )


with signal_col2:

    st.metric(
        "SUPERTREND / GRID BASE",
        (
            f"{SUPERTREND_PRICE:,.2f}"
            if SUPERTREND_PRICE is not None
            else "-"
        ),
    )


with signal_col3:

    if SIGNAL_TIME is not None:

        try:

            signal_time_text = (
                pd.Timestamp(
                    SIGNAL_TIME
                )
                .tz_convert(
                    "Asia/Kolkata"
                )
                .strftime(
                    "%d-%m-%Y %H:%M:%S IST"
                )
            )

        except Exception:

            signal_time_text = str(
                SIGNAL_TIME
            )

    else:

        signal_time_text = "-"


    st.metric(
        "SIGNAL TIME",
        signal_time_text,
    )


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:

        return "-"

    try:

        return f"{float(value):,.2f}"

    except Exception:

        return str(value)


# ============================================================
# FORMAT BTC
# ============================================================

def fmt_btc(value):

    if value is None:

        return "-"

    try:

        return f"{float(value):.3f} BTC"

    except Exception:

        return str(value)


# ============================================================
# ACCOUNT SUMMARY
# ============================================================

def show_account_summary(
    title,
    snapshot,
):

    st.markdown(
        f"## {title}"
    )


    if not snapshot:

        st.warning(
            f"{title}: data available नहीं है."
        )

        return


    direction = snapshot.get(
        "direction"
    )


    if direction == "LONG":

        direction_text = "🟢 LONG"

    elif direction == "SHORT":

        direction_text = "🔴 SHORT"

    else:

        direction_text = "-"


    col1, col2, col3, col4 = (
        st.columns(4)
    )


    with col1:

        st.metric(
            "DIRECTION",
            direction_text,
        )


    with col2:

        st.metric(
            "GRID BASE",
            fmt_price(
                snapshot.get(
                    "base_price"
                )
            ),
        )


    with col3:

        st.metric(
            "GRID DISTANCE",
            fmt_price(
                snapshot.get(
                    "grid_distance"
                )
            ),
        )


    with col4:

        st.metric(
            "LEVERAGE",
            (
                f"{MAIN_LEVERAGE}x"
                if title.startswith("MAIN")
                else f"{SUB_LEVERAGE}x"
            ),
        )


    col5, col6, col7, col8 = (
        st.columns(4)
    )


    with col5:

        st.metric(
            "GRID RANGE",
            fmt_price(
                snapshot.get(
                    "grid_range"
                )
            ),
        )


    with col6:

        st.metric(
            "GRID QUANTITY",
            str(
                snapshot.get(
                    "grid_quantity",
                    "-"
                )
            ),
        )


    with col7:

        st.metric(
            "ORDER QTY",
            fmt_btc(
                snapshot.get(
                    "order_qty_btc"
                )
            ),
        )


    with col8:

        st.metric(
            "ORDER CONTRACTS",
            str(
                snapshot.get(
                    "order_contracts",
                    "-"
                )
            ),
        )


    col9, col10, col11, col12 = (
        st.columns(4)
    )


    with col9:

        st.metric(
            "LOWER PRICE",
            fmt_price(
                snapshot.get(
                    "lower_price"
                )
            ),
        )


    with col10:

        st.metric(
            "UPPER PRICE",
            fmt_price(
                snapshot.get(
                    "upper_price"
                )
            ),
        )


    with col11:

        st.metric(
            "POSITION",
            fmt_btc(
                snapshot.get(
                    "position_size"
                )
            ),
        )


    with col12:

        st.metric(
            "POSITION ENTRY",
            fmt_price(
                snapshot.get(
                    "position_entry"
                )
            ),
        )


    col13, col14, col15, col16 = (
        st.columns(4)
    )


    with col13:

        st.metric(
            "REALIZED P&L",
            fmt_price(
                snapshot.get(
                    "realized_pnl"
                )
            ),
        )


    with col14:

        st.metric(
            "UNREALIZED P&L",
            fmt_price(
                snapshot.get(
                    "unrealized_pnl"
                )
            ),
        )


    with col15:

        st.metric(
            "PENDING ENTRIES",
            str(
                snapshot.get(
                    "pending_entries",
                    0
                )
            ),
        )


    with col16:

        st.metric(
            "PENDING TARGETS",
            str(
                snapshot.get(
                    "pending_targets",
                    0
                )
            ),
        )


# ============================================================
# ACCOUNT SNAPSHOTS
# ============================================================

MAIN_SNAPSHOT = BOT_DATA.get(
    "main"
)

SUB_SNAPSHOT = BOT_DATA.get(
    "sub"
)


show_account_summary(
    "MAIN ACCOUNT",
    MAIN_SNAPSHOT,
)


st.divider()


show_account_summary(
    "SUB ACCOUNT",
    SUB_SNAPSHOT,
)


# ============================================================
# CHART FUNCTION
# ============================================================

def create_account_chart(
    df,
    engine,
    current_price,
    account_title,
):

    fig = make_subplots(
        rows=1,
        cols=1,
    )


    if df is None or df.empty:

        fig.update_layout(
            title=account_title
        )

        return fig


    chart_df = df.tail(
        180
    ).copy()


    # ========================================================
    # CANDLESTICKS
    # ========================================================

    fig.add_trace(

        go.Candlestick(

            x=chart_df[
                "datetime"
            ],

            open=chart_df[
                "open"
            ],

            high=chart_df[
                "high"
            ],

            low=chart_df[
                "low"
            ],

            close=chart_df[
                "close"
            ],

            name="BTCUSD",
        )

    )


    # ========================================================
    # SUPERTREND
    # ========================================================

    buy_st = chart_df.copy()

    sell_st = chart_df.copy()


    buy_st.loc[
        buy_st[
            "supertrend_direction"
        ] >= 0,
        "supertrend"
    ] = np.nan


    sell_st.loc[
        sell_st[
            "supertrend_direction"
        ] <= 0,
        "supertrend"
    ] = np.nan


    fig.add_trace(

        go.Scatter(

            x=buy_st[
                "datetime"
            ],

            y=buy_st[
                "supertrend"
            ],

            mode="lines",

            name="BUY SUPERTREND",

            line=dict(
                width=3,
            ),
        )

    )


    fig.add_trace(

        go.Scatter(

            x=sell_st[
                "datetime"
            ],

            y=sell_st[
                "supertrend"
            ],

            mode="lines",

            name="SELL SUPERTREND",

            line=dict(
                width=3,
            ),
        )

    )


    # ========================================================
    # GRID LINES
    # ========================================================

    if engine is not None:

        for index, price in enumerate(
            engine.grid_prices
        ):

            price = float(price)


            # ------------------------------------------------
            # Determine line type
            # ------------------------------------------------

            if engine.direction == "LONG":

                if (
                    current_price is not None
                    and
                    price < current_price
                ):

                    line_name = (
                        f"BUY GRID {index}"
                    )

                else:

                    line_name = (
                        f"TARGET {index}"
                    )

            else:

                if (
                    current_price is not None
                    and
                    price > current_price
                ):

                    line_name = (
                        f"SELL GRID {index}"
                    )

                else:

                    line_name = (
                        f"TARGET {index}"
                    )


            fig.add_hline(

                y=price,

                line_width=1,

                annotation_text=(
                    f"{line_name} "
                    f"@ {price:,.2f}"
                ),

                annotation_position=(
                    "right"
                ),
            )


    # ========================================================
    # GRID BASE
    # ========================================================

    if engine is not None:

        if engine.base_price is not None:

            fig.add_hline(

                y=float(
                    engine.base_price
                ),

                line_width=4,

                annotation_text=(
                    "SUPERTREND GRID BASE "
                    f"@ "
                    f"{engine.base_price:,.2f}"
                ),

                annotation_position="left",
            )


    # ========================================================
    # CURRENT PRICE
    # ========================================================

    if current_price is not None:

        fig.add_hline(

            y=float(
                current_price
            ),

            line_width=3,

            annotation_text=(
                "LIVE PRICE "
                f"{current_price:,.2f}"
            ),

            annotation_position="left",
        )


    # ========================================================
    # POSITION ENTRY
    # ========================================================

    if (
        engine is not None
        and
        engine.position_entry is not None
    ):

        fig.add_hline(

            y=float(
                engine.position_entry
            ),

            line_width=3,

            annotation_text=(
                "ENTRY "
                f"{engine.position_entry:,.2f}"
            ),

            annotation_position="right",
        )


    # ========================================================
    # FILLED ORDERS
    # ========================================================

    if engine is not None:

        for key, order in (
            engine.filled_orders.items()
        ):

            fill_price = order.get(
                "fill_price"
            )


            if fill_price is None:

                continue


            side = str(
                order.get(
                    "side",
                    ""
                )
            ).upper()


            qty = order.get(
                "filled_qty_btc",
                order.get(
                    "qty_btc",
                    0
                ),
            )


            fig.add_hline(

                y=float(
                    fill_price
                ),

                line_width=4,

                annotation_text=(
                    f"FILLED {side} "
                    f"@ {float(fill_price):,.2f} "
                    f"| {float(qty):.3f} BTC"
                ),

                annotation_position="left",
            )


    # ========================================================
    # TARGETS
    # ========================================================

    if engine is not None:

        for target in (
            engine.targets.values()
        ):

            if target.get(
                "status"
            ) != "PENDING":

                continue


            target_price = target.get(
                "price"
            )


            if target_price is None:

                continue


            fig.add_hline(

                y=float(
                    target_price
                ),

                line_width=3,

                annotation_text=(
                    "TARGET "
                    f"@ {float(target_price):,.2f} "
                    f"| "
                    f"{float(target.get('qty_btc', 0)):.3f} BTC"
                ),

                annotation_position="right",
            )


    # ========================================================
    # LAYOUT
    # ========================================================

    fig.update_layout(

        title={
            "text":
                account_title,
            "x":
                0.5,
        },

        height=720,

        template="plotly_dark",

        xaxis_rangeslider_visible=False,

        hovermode="x unified",

        margin=dict(
            l=20,
            r=180,
            t=70,
            b=30,
        ),

        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
    )


    fig.update_xaxes(
        showgrid=True,
    )


    fig.update_yaxes(
        showgrid=True,
        fixedrange=False,
    )


    return fig


# ============================================================
# MAIN ACCOUNT CHART
# ============================================================

st.markdown(
    "## 🟢 MAIN ACCOUNT CHART"
)


MAIN_CHART = create_account_chart(

    CANDLE_DF,

    MAIN_ENGINE,

    CURRENT_PRICE,

    "MAIN ACCOUNT — BTCUSD 1H GRID",
)


st.plotly_chart(
    MAIN_CHART,
    use_container_width=True,
    key="main_account_chart",
)


# ============================================================
# MAIN ORDERS
# ============================================================

st.markdown(
    "### MAIN ACCOUNT — ORDER STATUS"
)


def engine_orders_dataframe(
    engine,
):

    rows = []


    if engine is None:

        return pd.DataFrame()


    for key, order in (
        engine.orders.items()
    ):

        rows.append(

            {

                "TYPE":
                    order.get(
                        "type",
                        ""
                    ),

                "SIDE":
                    str(
                        order.get(
                            "side",
                            ""
                        )
                    ).upper(),

                "PRICE":
                    order.get(
                        "price"
                    ),

                "QTY BTC":
                    order.get(
                        "qty_btc"
                    ),

                "CONTRACTS":
                    order.get(
                        "contracts"
                    ),

                "STATUS":
                    order.get(
                        "status"
                    ),

                "CLIENT ORDER ID":
                    order.get(
                        "client_order_id"
                    ),
            }
        )


    for key, target in (
        engine.targets.items()
    ):

        rows.append(

            {

                "TYPE":
                    "TARGET",

                "SIDE":
                    str(
                        target.get(
                            "side",
                            ""
                        )
                    ).upper(),

                "PRICE":
                    target.get(
                        "price"
                    ),

                "QTY BTC":
                    target.get(
                        "qty_btc"
                    ),

                "CONTRACTS":
                    target.get(
                        "contracts"
                    ),

                "STATUS":
                    target.get(
                        "status"
                    ),

                "CLIENT ORDER ID":
                    target.get(
                        "client_order_id"
                    ),
            }
        )


    if not rows:

        return pd.DataFrame()


    return pd.DataFrame(
        rows
    )


MAIN_ORDER_DF = (
    engine_orders_dataframe(
        MAIN_ENGINE
    )
)


if not MAIN_ORDER_DF.empty:

    st.dataframe(
        MAIN_ORDER_DF,
        use_container_width=True,
        hide_index=True,
    )

else:

    st.info(
        "MAIN account में अभी order state नहीं है."
    )


# ============================================================
# SUB ACCOUNT CHART
# ============================================================

st.divider()


st.markdown(
    "## 🔴 SUB ACCOUNT CHART"
)


SUB_CHART = create_account_chart(

    CANDLE_DF,

    SUB_ENGINE,

    CURRENT_PRICE,

    "SUB ACCOUNT — BTCUSD 1H GRID",
)


st.plotly_chart(
    SUB_CHART,
    use_container_width=True,
    key="sub_account_chart",
)


# ============================================================
# SUB ORDERS
# ============================================================

st.markdown(
    "### SUB ACCOUNT — ORDER STATUS"
)


SUB_ORDER_DF = (
    engine_orders_dataframe(
        SUB_ENGINE
    )
)


if not SUB_ORDER_DF.empty:

    st.dataframe(
        SUB_ORDER_DF,
        use_container_width=True,
        hide_index=True,
    )

else:

    st.info(
        "SUB account में अभी order state नहीं है."
    )


# ============================================================
# EXCHANGE ACCOUNT INFORMATION
# ============================================================

st.divider()


st.markdown(
    "## DELTA EXCHANGE ACCOUNT STATUS"
)


main_status_col, sub_status_col = (
    st.columns(2)
)


# ============================================================
# MAIN EXCHANGE STATUS
# ============================================================

with main_status_col:

    st.markdown(
        "### MAIN ACCOUNT"
    )


    if MAIN_API_KEY:

        st.success(
            "MAIN API KEY: CONNECTED"
        )

    else:

        st.warning(
            "MAIN API KEY: NOT CONFIGURED"
        )


    if LIVE_TRADING:

        st.error(
            "REAL TRADING: ON"
        )

    else:

        st.info(
            "REAL TRADING: OFF / TEST MODE"
        )


# ============================================================
# SUB EXCHANGE STATUS
# ============================================================

with sub_status_col:

    st.markdown(
        "### SUB ACCOUNT"
    )


    if SUB_API_KEY:

        st.success(
            "SUB API KEY: CONNECTED"
        )

    else:

        st.warning(
            "SUB API KEY: NOT CONFIGURED"
        )


    if LIVE_TRADING:

        st.error(
            "REAL TRADING: ON"
        )

    else:

        st.info(
            "REAL TRADING: OFF / TEST MODE"
        )


# ============================================================
# EXCHANGE POSITION / ORDER DATA
# ============================================================

if LIVE_TRADING:

    main_exchange_state = (
        get_account_exchange_state(
            MAIN_CLIENT
        )
    )


    sub_exchange_state = (
        get_account_exchange_state(
            SUB_CLIENT
        )
    )


else:

    main_exchange_state = {

        "positions":
            [],

        "orders":
            [],

        "position_error":
            None,

        "order_error":
            None,
    }


    sub_exchange_state = {

        "positions":
            [],

        "orders":
            [],

        "position_error":
            None,

        "order_error":
            None,
    }


# ============================================================
# EXCHANGE STATE EXPANDERS
# ============================================================

with st.expander(
    "MAIN ACCOUNT — RAW DELTA POSITION / ORDER DATA"
):

    st.json(
        main_exchange_state
    )


with st.expander(
    "SUB ACCOUNT — RAW DELTA POSITION / ORDER DATA"
):

    st.json(
        sub_exchange_state
    )


# ============================================================
# GRID SETTINGS DISPLAY
# ============================================================

st.divider()


st.markdown(
    "## GRID SETTINGS"
)


settings_col1, settings_col2 = (
    st.columns(2)
)


with settings_col1:

    st.markdown(
        "### MAIN ACCOUNT"
    )


    st.write(
        f"Direction Mode: "
        f"`{MAIN_DIRECTION_MODE}`"
    )


    st.write(
        f"Grid Range: "
        f"`{MAIN_GRID_RANGE_POINTS}` points"
    )


    st.write(
        f"Grid Quantity: "
        f"`{MAIN_GRID_QUANTITY}`"
    )


    st.write(
        f"Automatic Grid Distance: "
        f"`{MAIN_GRID_DISTANCE:.2f}` points"
    )


    st.write(
        f"Order Quantity: "
        f"`{MAIN_ORDER_QTY_BTC:.3f} BTC`"
    )


    st.write(
        f"Order Contracts: "
        f"`{MAIN_ORDER_CONTRACTS}`"
    )


    st.write(
        f"Total Grid BTC: "
        f"`{MAIN_TOTAL_BTC:.3f} BTC`"
    )


    st.write(
        f"Total Grid Contracts: "
        f"`{MAIN_TOTAL_CONTRACTS}`"
    )


with settings_col2:

    st.markdown(
        "### SUB ACCOUNT"
    )


    st.write(
        f"Direction Mode: "
        f"`{SUB_DIRECTION_MODE}`"
    )


    st.write(
        f"Grid Range: "
        f"`{SUB_GRID_RANGE_POINTS}` points"
    )


    st.write(
        f"Grid Quantity: "
        f"`{SUB_GRID_QUANTITY}`"
    )


    st.write(
        f"Automatic Grid Distance: "
        f"`{SUB_GRID_DISTANCE:.2f}` points"
    )


    st.write(
        f"Order Quantity: "
        f"`{SUB_ORDER_QTY_BTC:.3f} BTC`"
    )


    st.write(
        f"Order Contracts: "
        f"`{SUB_ORDER_CONTRACTS}`"
    )


    st.write(
        f"Total Grid BTC: "
        f"`{SUB_TOTAL_BTC:.3f} BTC`"
    )


    st.write(
        f"Total Grid Contracts: "
        f"`{SUB_TOTAL_CONTRACTS}`"
    )


# ============================================================
# SUPERTREND SETTINGS
# ============================================================

st.divider()


st.markdown(
    "## SUPERTREND SETTINGS"
)


st.write(
    f"Timeframe: "
    f"`1 Hour`"
)


st.write(
    f"ATR Length: "
    f"`{SUPERTREND_ATR_LENGTH}`"
)


st.write(
    f"Multiplier: "
    f"`{SUPERTREND_MULTIPLIER}`"
)


st.write(
    f"Source: "
    f"`{SUPERTREND_SOURCE}`"
)


# ============================================================
# LIVE MODE WARNING
# ============================================================

if LIVE_TRADING:

    st.error(
        """
        ⚠️ REAL TRADING MODE ACTIVE

        MAIN और SUB दोनों accounts पर real Delta
        orders भेजे जा सकते हैं।

        LIVE_TRADING को केवल पूरी testing के बाद ON करें।
        """
    )

else:

    st.success(
        """
        TEST MODE ACTIVE

        LIVE_TRADING = False

        अभी real Delta orders नहीं भेजे जा रहे हैं।
        """
    )


# ============================================================
# LAST UPDATE
# ============================================================

now_utc = pd.Timestamp.now(
    tz="UTC"
)


try:

    now_ist = (
        now_utc
        .tz_convert(
            "Asia/Kolkata"
        )
        .strftime(
            "%d-%m-%Y %H:%M:%S IST"
        )
    )

except Exception:

    now_ist = str(
        now_utc
    )


st.caption(
    f"Last Dashboard Update: {now_ist}"
)


# ============================================================
# AUTO REFRESH
# ============================================================

if AUTO_REFRESH:

    time.sleep(
        POLL_SECONDS
    )

    st.rerun()


# ============================================================
# PART 4 READY
# ============================================================

PART_4_READY = True
