"""家計簿ダッシュボード — Streamlit 単一ファイルアプリ"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any

import pandas as pd
import streamlit as st
from supabase import create_client, Client
import plotly.express as px
import streamlit as st

# Supabase 接続の初期化
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["supabase"]["SUPABASE_URL"]
    key = st.secrets["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# =========================================================
# ページ設定
# =========================================================
st.set_page_config(page_title="家計簿アプリ", layout="wide")

# =========================================================
# 定数・マスター定義
# =========================================================
DATA_FILE = "household_expenses.csv"
INCOME_FILE = "household_income.csv"
FIXED_FILE = "household_fixed_monthly.csv"
VAR_BUDGET_FILE = "household_variable_budgets.csv"
FIXED_BUDGET_FILE = "household_fixed_budgets.csv"
SETTINGS_FILE = "household_settings.json"

GROUP_ORDER = ["生活費", "個人支出", "お小遣い"]

CATEGORIES: dict[str, list[str]] = {
    "生活費": ["食費", "外食費", "日用品", "雑費"],
    "個人支出": ["家具家電", "交通費", "美容費", "衣服", "医療費", "特別費", "その他個人支出"],
    "お小遣い": ["趣味", "交際費", "娯楽雑費"],
}

ALL_CATEGORIES = [cat for group in GROUP_ORDER for cat in CATEGORIES[group]]
CATEGORY_TO_GROUP = {cat: group for group, cats in CATEGORIES.items() for cat in cats}

# 毎月定額を積み立て、貯まったら使うカテゴリ（予算＝月次積立額）
SINKING_FUND_CATEGORIES = {"家具家電"}
# 区分全体を1つの積立口座として扱う
SINKING_FUND_GROUPS = {"お小遣い"}

DEFAULT_VAR_BUDGET = {
    "食費": 30000,
    "外食費": 15000,
    "日用品": 10000,
    "雑費": 3000,
    "家具家電": 25000,
    "交通費": 10000,
    "美容費": 5000,
    "衣服": 5000,
    "医療費": 3000,
    "特別費": 10000,
    "その他個人支出": 3000,
    "趣味": 5000,
    "交際費": 10000,
    "娯楽雑費": 5000,
}

DEFAULT_FIXED_BUDGET = {
    "家賃": 25000,
    "ガス": 2000,
    "電気": 7000,
    "水道": 1000,
    "通信費": 3000,
    "薬・サプリ": 3000,
    "保険料": 11000,
    "サブスク": 3000,
}

PAYMENT_METHODS = [
    "現金",
    "ID",
    "PayPay",
    "クレカ（EPOS）",
    "クレカ（ヨドバシ）",
    "クレカ（楽天）",
]

INCOME_TYPES = ["本業", "副業", "臨時収入", "その他"]

EXP_COLUMNS = ["日付", "区分", "カテゴリ", "内容", "金額", "支払い方法", "集計月", "備考"]
INC_COLUMNS = ["日付", "種別", "金額", "集計月", "備考"]
FIX_COLUMNS = ["集計月", "項目", "金額", "備考"]

# =========================================================
# ユーティリティ
# =========================================================
def clean_text(value: Any, default: str = "-") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    text = str(value).strip()
    return text if text else default


def to_int_amount(value: Any) -> int:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0
        return int(pd.to_numeric(value, errors="coerce") or 0)
    except (TypeError, ValueError):
        return 0


def to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return pd.to_datetime(value).date()


def month_key(d: date | datetime | str) -> str:
    if isinstance(d, str):
        return d[:7]
    if isinstance(d, datetime):
        return d.strftime("%Y-%m")
    return d.strftime("%Y-%m")


def ensure_month_column(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "日付" in df.columns:
        df = df.copy()
        df["集計月"] = df["日付"].map(lambda x: month_key(to_date(x)))
    return df


def rate_band(rate: float) -> str:
    """使用率の帯ラベル（色分け用）。"""
    r = float(rate)
    if r <= 0:
        return "0%"
    if r < 25:
        return "1–24%"
    if r < 50:
        return "25–49%"
    if r < 75:
        return "50–74%"
    if r < 90:
        return "75–89%"
    if r <= 100:
        return "90–100%"
    return "100%超"


RATE_BAND_COLORS = {
    "0%": "#AED6F1",
    "1–24%": "#1E8449",
    "25–49%": "#58D68D",
    "50–74%": "#F7DC6F",
    "75–89%": "#F5B041",
    "90–100%": "#E67E22",
    "100%超": "#C0392B",
}

RATE_BAND_ORDER = list(RATE_BAND_COLORS.keys())


def rate_color(rate: float) -> str:
    return RATE_BAND_COLORS[rate_band(rate)]


def rate_text_color(rate: float) -> str:
    """背景色に対して読みやすい文字色。"""
    band = rate_band(rate)
    # 明るい帯は黒、暗い帯は白
    if band in ("0%", "25–49%", "50–74%", "75–89%"):
        return "#1a1a1a"
    return "#ffffff"


def style_budget_table(df: pd.DataFrame, rate_col: str = "使用率(%)"):
    """使用率は帯ごとの背景色、残予算マイナスは赤文字。"""

    def _rate_style(val):
        r = float(val)
        return (
            f"background-color: {rate_color(r)}; "
            f"color: {rate_text_color(r)}; font-weight: 700; text-align: center"
        )

    def _remain_style(val):
        if float(val) < 0:
            return "color: #C0392B; font-weight: 700"
        return ""

    format_map = {
        "予算": "¥{:,.0f}",
        "実績": "¥{:,.0f}",
        "残予算": "¥{:,.0f}",
        rate_col: "{:.1f}%",
    }
    styled = df.style.format({k: v for k, v in format_map.items() if k in df.columns})
    if rate_col in df.columns:
        styled = styled.map(_rate_style, subset=[rate_col])
    if "残予算" in df.columns:
        styled = styled.map(_remain_style, subset=["残予算"])
    return styled


def yen(n: float | int) -> str:
    return f"¥{int(n):,}"


def iter_months(start: str, end: str) -> list[str]:
    """YYYY-MM を start〜end（含む）で列挙。"""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out: list[str] = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


# --- Supabase データ読み書き関数 ---

def load_expenses() -> pd.DataFrame:
    """支出データの取得"""
    columns_map = {
        "date": "日付",
        "group_name": "区分",
        "category": "カテゴリ",
        "content": "内容",
        "amount": "金額",
        "payment_method": "支払い方法",
        "month_key": "集計月",
        "note": "備考"
    }
    res = supabase.table("expenses").select("*").execute()
    data = res.data
    if not data:
        return pd.DataFrame(columns=list(columns_map.values()))
    df = pd.DataFrame(data)
    df = df.rename(columns=columns_map)
    for col in columns_map.values():
        if col not in df.columns:
            df[col] = None
    return df

def load_income() -> pd.DataFrame:
    """収入データの取得"""
    columns_map = {
        "date": "日付",
        "type": "区分",
        "amount": "金額",
        "month_key": "集計月",
        "note": "備考"
    }
    res = supabase.table("income").select("*").execute()
    data = res.data
    if not data:
        return pd.DataFrame(columns=list(columns_map.values()))
    df = pd.DataFrame(data)
    df = df.rename(columns=columns_map)
    for col in columns_map.values():
        if col not in df.columns:
            df[col] = None
    return df

def save_expense_item(date_str: str, group: str, category: str, content: str, amount: int, pay_method: str, month_key: str, note: str):
    """支出データの新規保存"""
    payload = {
        "date": date_str,
        "group_name": group,
        "category": category,
        "content": content,
        "amount": amount,
        "payment_method": pay_method,
        "month_key": month_key,
        "note": note
    }
    supabase.table("expenses").insert(payload).execute()

def delete_expense_item(expense_id: int):
    """支出データの削除"""
    supabase.table("expenses").delete().eq("id", expense_id).execute()

def load_app_settings() -> dict:
    """設定の読み込み"""
    res = supabase.table("app_settings").select("value").eq("key", "main_settings").execute()
    if res.data:
        return res.data[0]["value"]
    return {}

def save_app_settings(settings_dict: dict):
    """設定の保存"""
    payload = {"key": "main_settings", "value": settings_dict}
    supabase.table("app_settings").upsert(payload).execute()


def load_fixed_monthly() -> pd.DataFrame:
    """固定費（確定額）データの取得"""
    columns_map = {
        "month_key": "集計月",
        "item": "項目",
        "amount": "金額",
        "note": "備考"
    }
    
    res = supabase.table("fixed_monthly").select("*").execute()
    data = res.data
    
    # データが存在しない場合は空のデータフレームを正しいカラム構造で作成
    if not data:
        return pd.DataFrame(columns=list(columns_map.values()))
    
    df = pd.DataFrame(data)
    
    # カラムのリネーム（不足しているカラムがあれば補完）
    df = df.rename(columns=columns_map)
    for col in columns_map.values():
        if col not in df.columns:
            df[col] = None
            
    return df

def load_data():
    """支出・収入・固定費をまとめて読み込む"""
    df_exp = load_expenses()
    df_inc = load_income()
    df_fix = load_fixed_monthly()
    return df_exp, df_inc, df_fix

# --- 予算マスター（変動費・固定費）の読み書き関数 ---

def load_var_budgets() -> dict[str, int]:
    """変動費予算の取得"""
    res = supabase.table("variable_budgets").select("category, budget").execute()
    data = res.data
    if not data:
        # 初期設定がない場合のデフォルト予算（必要に応じて調整してください）
        return {cat: 10000 for group in GROUP_ORDER for cat in CATEGORIES[group]}
    return {row["category"]: int(row["budget"]) for row in data}

def save_var_budgets(var_budgets: dict[str, int]):
    """変動費予算の保存"""
    records = []
    for group in GROUP_ORDER:
        for cat in CATEGORIES[group]:
            if cat in var_budgets:
                records.append({
                    "category": cat,
                    "budget": int(var_budgets[cat]),
                    "group_name": group
                })
    if records:
        supabase.table("variable_budgets").upsert(records).execute()


def load_fixed_budgets() -> dict[str, int]:
    """固定費標準予算の取得"""
    res = supabase.table("fixed_budgets").select("item, budget").execute()
    data = res.data
    if not data:
        return {}
    return {row["item"]: int(row["budget"]) for row in data}

def save_fixed_budgets(fixed_budgets: dict[str, int]):
    """固定費標準予算の保存"""
    records = [{"item": k, "budget": int(v)} for k, v in fixed_budgets.items()]
    if records:
        supabase.table("fixed_budgets").upsert(records).execute()

# --- アプリ設定（JSON）の読み書き関数 ---

def load_settings() -> dict:
    """設定の読み込み（Supabaseのapp_settingsテーブルから取得）"""
    try:
        res = supabase.table("app_settings").select("value").eq("key", "main_settings").execute()
        if res.data and len(res.data) > 0:
            return res.data[0]["value"]
    except Exception as e:
        st.error(f"設定の読み込みエラー: {e}")
    return {}

def save_settings(settings_dict: dict):
    """設定の保存（Supabaseのapp_settingsテーブルへ保存）"""
    try:
        payload = {"key": "main_settings", "value": settings_dict}
        supabase.table("app_settings").upsert(payload).execute()
    except Exception as e:
        st.error(f"設定の保存エラー: {e}")


# =========================================================
# CRUD 共通ヘルパー
# =========================================================
def append_expense(row: dict[str, Any]) -> None:
    new_df = pd.DataFrame([row], columns=EXP_COLUMNS)
    st.session_state.df_exp = pd.concat([st.session_state.df_exp, new_df], ignore_index=True)
    save_all_data()


def append_income(row: dict[str, Any]) -> None:
    new_df = pd.DataFrame([row], columns=INC_COLUMNS)
    st.session_state.df_inc = pd.concat([st.session_state.df_inc, new_df], ignore_index=True)
    save_all_data()


def apply_edits(
    source_df: pd.DataFrame,
    edited_df: pd.DataFrame,
    orig_indices: pd.Index,
    cols: list[str],
    *,
    sync_category_group: bool = False,
) -> tuple[pd.DataFrame, bool]:
    """行単位で差分を反映。orig_indices は元 df の index。"""
    changed = False
    df = source_df.copy()

    for i, orig_idx in enumerate(orig_indices):
        if i >= len(edited_df):
            break
        for col in cols:
            new_val = edited_df.iloc[i][col]
            old_val = df.loc[orig_idx, col]

            if col == "日付":
                new_val = to_date(new_val)
                old_val = to_date(old_val)
            elif col == "金額":
                new_val = to_int_amount(new_val)
                old_val = to_int_amount(old_val)
            elif col in ("内容", "備考"):
                new_val = clean_text(new_val)
                old_val = clean_text(old_val)

            if str(new_val) != str(old_val):
                df.loc[orig_idx, col] = new_val
                if col == "日付":
                    df.loc[orig_idx, "集計月"] = month_key(new_val)
                changed = True

        if sync_category_group:
            cat = str(df.loc[orig_idx, "カテゴリ"])
            if cat in CATEGORY_TO_GROUP:
                correct_group = CATEGORY_TO_GROUP[cat]
                if df.loc[orig_idx, "区分"] != correct_group:
                    df.loc[orig_idx, "区分"] = correct_group
                    changed = True
            else:
                # 不正カテゴリは区分に合わせて先頭カテゴリへ
                group = str(df.loc[orig_idx, "区分"])
                if group not in CATEGORIES:
                    group = GROUP_ORDER[0]
                df.loc[orig_idx, "区分"] = group
                df.loc[orig_idx, "カテゴリ"] = CATEGORIES[group][0]
                changed = True

    return df, changed


def delete_by_positions(source_df: pd.DataFrame, orig_indices: pd.Index, selected_positions: list[int]) -> pd.DataFrame:
    """edited_df の行位置 (0-based) から元 index を特定して削除。"""
    to_drop = [orig_indices[pos] for pos in selected_positions if 0 <= pos < len(orig_indices)]
    return source_df.drop(to_drop).reset_index(drop=True)


# =========================================================
# 集計ロジック
# =========================================================
def resolve_sinking_start(name: str, selected_month: str, settings: dict[str, Any]) -> str:
    key = f"積立開始月_{name}"
    start = str(settings.get(key, "") or "").strip()
    if start:
        return start[:7]
    return selected_month

def furniture_monthly_contrib(settings: dict[str, Any], var_budgets: dict[str, int]) -> int:
    """家具家電の月次積立額を settings から最優先で取得。なければ var_budgets を参照。"""
    if "積立額_家具家電" in settings:
        return int(settings["積立額_家具家電"])
    return int(var_budgets.get("家具家電", 0))


def allowance_monthly_contrib(settings: dict[str, Any], var_budgets: dict[str, int]) -> int:
    """お小遣いの月次積立額を settings から最優先で取得。"""
    if "積立額_お小遣い" in settings:
        return int(settings["積立額_お小遣い"])
    return sum(int(var_budgets.get(cat, 0)) for cat in CATEGORIES["お小遣い"])

def calc_sinking_fund(
    df_exp: pd.DataFrame,
    monthly_contrib: int,
    up_to_month: str,
    start_month: str,
    *,
    category: str | None = None,
    group: str | None = None,
) -> dict[str, int | float]:
    """
    積立の累計状況。
    毎月の拠出額を積み上げ、該当カテゴリ/区分の支出を差し引いた残高を返す。
    """
    if not start_month:
        start_month = up_to_month
    if start_month > up_to_month:
        start_month = up_to_month

    months = iter_months(start_month, up_to_month)
    contributed = len(months) * int(monthly_contrib)

    if df_exp.empty:
        spent = 0
        month_spent = 0
    else:
        # 開始月から選択月までの範囲を対象
        base_mask = (df_exp["集計月"] >= start_month) & (df_exp["集計月"] <= up_to_month)
        month_mask = df_exp["集計月"] == up_to_month

        if category is not None:
            # カテゴリ名の一致判定（文字列の前後の空白を無視）
            cat_mask = df_exp["カテゴリ"].astype(str).str.strip() == category
            spent = int(df_exp.loc[base_mask & cat_mask, "金額"].sum())
            month_spent = int(df_exp.loc[month_mask & cat_mask, "金額"].sum())
        elif group is not None:
            # 区分名の一致判定、またはお小遣いグループ内カテゴリの判定
            grp_mask = df_exp["区分"].astype(str).str.strip() == group
            if group in CATEGORIES:
                cat_in_grp_mask = df_exp["カテゴリ"].astype(str).str.strip().isin(CATEGORIES[group])
                grp_mask = grp_mask | cat_in_grp_mask

            spent = int(df_exp.loc[base_mask & grp_mask, "金額"].sum())
            month_spent = int(df_exp.loc[month_mask & grp_mask, "金額"].sum())
        else:
            raise ValueError("category または group を指定してください")

    balance = contributed - spent
    cum_rate = round((spent / contributed) * 100, 1) if contributed > 0 else 0.0

    return {
        "months": len(months),
        "contributed": contributed,
        "spent": spent,
        "balance": balance,
        "month_spent": month_spent,
        "monthly": int(monthly_contrib),
        "cum_rate": cum_rate,
        "start_month": start_month,
    }


def calc_savings_reserve(
    up_to_month: str,
    settings: dict[str, Any],
) -> dict[str, int | float | str]:
    """月次貯蓄額 × 経過月数の確保額。"""
    monthly = int(settings.get("月次貯蓄額", 0) or 0)
    start = str(settings.get("貯蓄開始月", "") or "").strip()[:7]
    if not start:
        start = up_to_month
    if start > up_to_month:
        start = up_to_month
    months = iter_months(start, up_to_month) if monthly > 0 else []
    reserved = len(months) * monthly
    return {
        "monthly": monthly,
        "months": len(months),
        "reserved": reserved,
        "start_month": start,
    }


def calc_raw_net_through(
    df_exp: pd.DataFrame,
    df_inc: pd.DataFrame,
    df_fix: pd.DataFrame,
    up_to_month: str,
    fixed_budgets: dict[str, int],
    *,
    before: bool = False,
) -> int:
    """up_to_month までの（または未満の）生の累計収支。"""
    all_m = collect_months(df_exp, df_inc, df_fix)
    if before:
        target = [m for m in all_m if m < up_to_month]
    else:
        target = [m for m in all_m if m <= up_to_month]
    return sum(month_totals(df_exp, df_inc, df_fix, m, fixed_budgets)["net"] for m in target)


def free_cash_as_of(
    df_exp: pd.DataFrame,
    df_inc: pd.DataFrame,
    df_fix: pd.DataFrame,
    up_to_month: str,
    fixed_budgets: dict[str, int],
    var_budgets: dict[str, int],
    settings: dict[str, Any],
) -> dict[str, int]:
    """
    手元の自由資金。
    生の累計収支から、家具家電積立残高・お小遣い積立残高・貯蓄確保額（いずれもプラス分）を引く。
    """
    raw = calc_raw_net_through(df_exp, df_inc, df_fix, up_to_month, fixed_budgets, before=False)

    furn_monthly = furniture_monthly_contrib(settings, var_budgets)
    furn = calc_sinking_fund(
        df_exp,
        furn_monthly,
        up_to_month,
        resolve_sinking_start("家具家電", up_to_month, settings),
        category="家具家電",
    )
    allow_monthly = allowance_monthly_contrib(settings, var_budgets)
    allow = calc_sinking_fund(
        df_exp,
        allow_monthly,
        up_to_month,
        resolve_sinking_start("お小遣い", up_to_month, settings),
        group="お小遣い",
    )
    sav = calc_savings_reserve(up_to_month, settings)

    reserved_furniture = max(int(furn["balance"]), 0)
    reserved_allowance = max(int(allow["balance"]), 0)
    reserved_savings = int(sav["reserved"])
    reserved_total = reserved_furniture + reserved_allowance + reserved_savings
    free = raw - reserved_total

    return {
        "raw": raw,
        "free": free,
        "reserved_furniture": reserved_furniture,
        "reserved_allowance": reserved_allowance,
        "reserved_savings": reserved_savings,
        "reserved_total": reserved_total,
    }


def build_category_budget_df(
    m_exp: pd.DataFrame,
    var_budgets: dict[str, int],
    df_exp: pd.DataFrame,
    selected_month: str,
    settings: dict[str, Any],
) -> pd.DataFrame:
    furn_monthly = furniture_monthly_contrib(settings, var_budgets)
    furn_fund = calc_sinking_fund(
        df_exp,
        furn_monthly,
        selected_month,
        resolve_sinking_start("家具家電", selected_month, settings),
        category="家具家電",
    )
    allow_fund = calc_sinking_fund(
        df_exp,
        allowance_monthly_contrib(settings, var_budgets),
        selected_month,
        resolve_sinking_start("お小遣い", selected_month, settings),
        group="お小遣い",
    )

    records = []
    for group in GROUP_ORDER:
        for cat in CATEGORIES[group]:
            budget_val = int(var_budgets.get(cat, 0))
            actual_val = int(m_exp.loc[m_exp["カテゴリ"] == cat, "金額"].sum()) if not m_exp.empty else 0

            if cat in SINKING_FUND_CATEGORIES:
                records.append(
                    {
                        "区分": group,
                        "カテゴリ": cat,
                        "運用": "積立",
                        "予算": furn_monthly,
                        "実績": actual_val,
                        "残予算": int(furn_fund["balance"]),      # ← fund を furn_fund に修正
                        "使用率(%)": float(furn_fund["cum_rate"]), # ← fund を furn_fund に修正
                    }
                )
            elif group in SINKING_FUND_GROUPS:
                records.append(
                    {
                        "区分": group,
                        "カテゴリ": cat,
                        "運用": "積立",
                        "予算": budget_val,
                        "実績": actual_val,
                        "残予算": int(allow_fund["balance"]),
                        "使用率(%)": float(allow_fund["cum_rate"]),
                    }
                )
            else:
                rate = round((actual_val / budget_val) * 100, 1) if budget_val > 0 else 0.0
                records.append(
                    {
                        "区分": group,
                        "カテゴリ": cat,
                        "運用": "通常",
                        "予算": budget_val,
                        "実績": actual_val,
                        "残予算": budget_val - actual_val,
                        "使用率(%)": rate,
                    }
                )
    return pd.DataFrame(records)


def build_group_budget_df(
    budget_df: pd.DataFrame,
    allowance_fund: dict[str, int | float] | None = None,
) -> pd.DataFrame:
    # 区分集計は「今月の予算・実績」ベース
    g = (
        budget_df.groupby("区分", as_index=False)
        .agg(予算=("予算", "sum"), 実績=("実績", "sum"))
        .assign(残予算=lambda x: x["予算"] - x["実績"])
    )
    g["使用率(%)"] = g.apply(
        lambda r: round((r["実績"] / r["予算"]) * 100, 1) if r["予算"] > 0 else 0.0,
        axis=1,
    )
    # お小遣いは区分積立残高・累計使用率で上書き
    if allowance_fund is not None and "お小遣い" in set(g["区分"]):
        mask = g["区分"] == "お小遣い"
        g.loc[mask, "残予算"] = int(allowance_fund["balance"])
        g.loc[mask, "使用率(%)"] = float(allowance_fund["cum_rate"])
    g["区分"] = pd.Categorical(g["区分"], categories=GROUP_ORDER, ordered=True)
    return g.sort_values("区分").reset_index(drop=True)


def usage_bar_chart(df: pd.DataFrame, y_col: str, title: str = "", height: int = 200):
    """使用率横棒（帯ごとの離散色）。"""
    plot_df = df.copy()
    plot_df["使用帯"] = plot_df["使用率(%)"].map(rate_band)
    plot_df["使用帯"] = pd.Categorical(plot_df["使用帯"], categories=RATE_BAND_ORDER, ordered=True)

    y_order = GROUP_ORDER if y_col == "区分" else ALL_CATEGORIES
    fig = px.bar(
        plot_df,
        x="使用率(%)",
        y=y_col,
        text="使用率(%)",
        orientation="h",
        color="使用帯",
        color_discrete_map=RATE_BAND_COLORS,
        category_orders={y_col: y_order, "使用帯": RATE_BAND_ORDER},
        title=title or None,
    )
    fig.update_layout(yaxis={"categoryorder": "array", "categoryarray": y_order})
    fig.add_vline(x=100, line_dash="dash", line_color="#C0392B", annotation_text="100%")
    fig.update_traces(texttemplate="%{text}%", textposition="outside")
    xmax = max(120.0, float(plot_df["使用率(%)"].max()) + 15 if len(plot_df) else 120.0)
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=40, t=40 if title else 10, b=10),
        xaxis_range=[0, xmax],
        legend_title_text="使用帯",
    )
    return fig


def default_fixed_rows(month: str, fixed_budgets: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"集計月": month, "項目": item, "金額": amount, "備考": "標準目安額"}
            for item, amount in fixed_budgets.items()
        ]
    )


def get_fixed_for_month(df_fix: pd.DataFrame, month: str, fixed_budgets: dict[str, int]) -> tuple[pd.DataFrame, bool]:
    """表示用固定費。未登録月はデフォルトを返し、is_virtual=True。"""
    current = df_fix[df_fix["集計月"] == month].copy()
    if current.empty:
        return default_fixed_rows(month, fixed_budgets), True
    return current, False


def persist_fixed_month(edited: pd.DataFrame, month: str, is_virtual: bool, orig_indices: pd.Index | None) -> None:
    if is_virtual:
        # 仮想データを実データとして追加
        new_rows = edited.copy()
        new_rows["集計月"] = month
        new_rows["金額"] = new_rows["金額"].map(to_int_amount)
        new_rows["備考"] = new_rows["備考"].map(lambda x: clean_text(x))
        st.session_state.df_fix = pd.concat(
            [st.session_state.df_fix, new_rows[FIX_COLUMNS]],
            ignore_index=True,
        )
        save_all_data()
        return

    assert orig_indices is not None
    df, changed = apply_edits(
        st.session_state.df_fix,
        edited,
        orig_indices,
        ["金額", "備考"],
    )
    if changed:
        st.session_state.df_fix = df
        save_all_data()


def month_totals(
    df_exp: pd.DataFrame,
    df_inc: pd.DataFrame,
    df_fix: pd.DataFrame,
    month: str,
    fixed_budgets: dict[str, int],
) -> dict[str, int]:
    m_exp = df_exp[df_exp["集計月"] == month] if not df_exp.empty else pd.DataFrame()
    m_inc = df_inc[df_inc["集計月"] == month] if not df_inc.empty else pd.DataFrame()
    fix_df, _ = get_fixed_for_month(df_fix, month, fixed_budgets)
    income = int(m_inc["金額"].sum()) if not m_inc.empty else 0
    fixed = int(fix_df["金額"].sum()) if not fix_df.empty else 0
    variable = int(m_exp["金額"].sum()) if not m_exp.empty else 0
    net = income - fixed - variable
    return {"income": income, "fixed": fixed, "variable": variable, "net": net}


def collect_months(df_exp: pd.DataFrame, df_inc: pd.DataFrame, df_fix: pd.DataFrame) -> list[str]:
    months: set[str] = set()
    for df in (df_exp, df_inc, df_fix):
        if not df.empty and "集計月" in df.columns:
            months.update(df["集計月"].dropna().astype(str).tolist())
    
    # 今月を取得
    today_str = datetime.today().strftime("%Y-%m")
    months.add(today_str)

    # 登録・選択されている最新月を取得し、その「1か月後」も選択肢に追加
    max_month_str = max(months)
    y, m = map(int, max_month_str.split("-"))
    m += 1
    if m > 12:
        m = 1
        y += 1
    next_month_str = f"{y:04d}-{m:02d}"
    months.add(next_month_str)

    return sorted(months, reverse=True)


def calc_carryover(
    df_exp: pd.DataFrame,
    df_inc: pd.DataFrame,
    df_fix: pd.DataFrame,
    selected_month: str,
    fixed_budgets: dict[str, int],
    var_budgets: dict[str, int],
    settings: dict[str, Any],
) -> int:
    """
    選択月より前までの自由資金繰越。
    前月時点の生収支累計から、積立残高・貯蓄確保を差し引く。
    """
    all_m = collect_months(df_exp, df_inc, df_fix)
    prior = sorted(m for m in all_m if m < selected_month)
    if not prior:
        return 0
    prev_month = prior[-1]
    return free_cash_as_of(
        df_exp, df_inc, df_fix, prev_month, fixed_budgets, var_budgets, settings
    )["free"]


def build_monthly_trend(
    df_exp: pd.DataFrame,
    df_inc: pd.DataFrame,
    df_fix: pd.DataFrame,
    fixed_budgets: dict[str, int],
) -> pd.DataFrame:
    months = sorted(collect_months(df_exp, df_inc, df_fix))
    rows = []
    for m in months:
        t = month_totals(df_exp, df_inc, df_fix, m, fixed_budgets)
        rows.append(
            {
                "集計月": m,
                "収入": t["income"],
                "固定費": t["fixed"],
                "変動費": t["variable"],
                "収支": t["net"],
            }
        )
    return pd.DataFrame(rows)


# =========================================================
# セッション初期化
# =========================================================
if "df_exp" not in st.session_state:
    st.session_state.df_exp, st.session_state.df_inc, st.session_state.df_fix = load_data()
    # スキーマ移行（支払い方法列など）をディスクへ反映
    st.session_state.df_exp.to_csv(DATA_FILE, index=False)
    st.session_state.df_inc.to_csv(INCOME_FILE, index=False)
    st.session_state.df_fix.to_csv(FIXED_FILE, index=False)
if "var_budgets" not in st.session_state:
    st.session_state.var_budgets = load_var_budgets()
    if not os.path.exists(VAR_BUDGET_FILE):
        save_var_budgets(st.session_state.var_budgets)
if "fixed_budgets" not in st.session_state:
    st.session_state.fixed_budgets = load_fixed_budgets()
    if not os.path.exists(FIXED_BUDGET_FILE):
        save_fixed_budgets(st.session_state.fixed_budgets)
if "settings" not in st.session_state:
    st.session_state.settings = load_settings()
    if not os.path.exists(SETTINGS_FILE):
        save_settings(st.session_state.settings)

df_exp: pd.DataFrame = st.session_state.df_exp
df_inc: pd.DataFrame = st.session_state.df_inc
df_fix: pd.DataFrame = st.session_state.df_fix
var_budgets: dict[str, int] = st.session_state.var_budgets
fixed_budgets: dict[str, int] = st.session_state.fixed_budgets
settings: dict[str, Any] = st.session_state.settings

# =========================================================
# 登録ダイアログ
# =========================================================
@st.dialog("変動費を登録")
def dialog_add_expense(default_month: str):
    today = datetime.today().date()
    try:
        default_date = date.fromisoformat(f"{default_month}-01")
        if default_month == today.strftime("%Y-%m"):
            default_date = today
    except ValueError:
        default_date = today

    d = st.date_input("日付", default_date)
    group = st.selectbox("区分", GROUP_ORDER)
    category = st.selectbox("カテゴリ", CATEGORIES[group])
    st.caption("区分を変えると、選べるカテゴリも切り替わります。")
    item_name = st.text_input("内容（店舗・目的など）", placeholder="例: セブンイレブン")
    amount = st.number_input("金額（円）", min_value=0, step=100, value=0)
    pay = st.selectbox("支払い方法", PAYMENT_METHODS)
    note = st.text_input("備考", placeholder="補足があれば入力")

    if st.button("登録する", type="primary", use_container_width=True):
        append_expense(
            {
                "日付": d,
                "区分": group,
                "カテゴリ": category,
                "内容": clean_text(item_name),
                "金額": int(amount),
                "支払い方法": pay,
                "集計月": month_key(d),
                "備考": clean_text(note),
            }
        )
        st.success("支出を登録しました")
        st.rerun()


@st.dialog("収入を登録")
def dialog_add_income(default_month: str):
    months = collect_months(
        st.session_state.df_exp, st.session_state.df_inc, st.session_state.df_fix
    )
    # 現在選択されている月をデフォルト選択位置にする
    default_idx = months.index(default_month) if default_month in months else 0

    target_month = st.selectbox("対象の月（何月分）", months, index=default_idx)
    inc_type = st.selectbox("収入種別", INCOME_TYPES)
    amount = st.number_input(
        "金額（円）", min_value=0, step=1000, value=0, key="dlg_inc_amount"
    )
    note = st.text_input(
        "備考", placeholder="例: 8/25給料（9月分として計上）", key="dlg_inc_note"
    )

    if st.button(
        "登録する", type="primary", use_container_width=True, key="dlg_inc_submit"
    ):
        # 内部的な「日付」列にはその月の1日（例: 2026-09-01）を割り当て
        dummy_date = date.fromisoformat(f"{target_month}-01")
        append_income(
            {
                "日付": dummy_date,
                "種別": inc_type,
                "金額": int(amount),
                "集計月": target_month,
                "備考": clean_text(note),
            }
        )
        st.success(f"{target_month} 分の収入を登録しました")
        st.rerun()

# =========================================================
# ヘッダー・月選択・登録ボタン
# =========================================================
st.title("家計簿ダッシュボード")

months = collect_months(df_exp, df_inc, df_fix)

# 当月（YYYY-MM）をデフォルト選択位置にする
today_m = datetime.today().strftime("%Y-%m")
default_m_idx = months.index(today_m) if today_m in months else 0

head_l, head_r = st.columns([2, 1])
with head_l:
    selected_month = st.selectbox("集計月", months, index=default_m_idx)

with head_r:
    st.write("")
    st.write("")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("＋ 支出", use_container_width=True, type="primary"):
            dialog_add_expense(selected_month)
    with b2:
        if st.button("＋ 収入", use_container_width=True):
            dialog_add_income(selected_month)

# 当月データ
m_exp = df_exp[df_exp["集計月"] == selected_month].copy() if not df_exp.empty else pd.DataFrame(columns=EXP_COLUMNS)
m_inc = df_inc[df_inc["集計月"] == selected_month].copy() if not df_inc.empty else pd.DataFrame(columns=INC_COLUMNS)
current_fix_df, fix_is_virtual = get_fixed_for_month(df_fix, selected_month, fixed_budgets)

totals = month_totals(df_exp, df_inc, df_fix, selected_month, fixed_budgets)
total_income = totals["income"]
total_fixed = totals["fixed"]
total_variable = totals["variable"]
total_spend = total_fixed + total_variable
net_balance = totals["net"]

# 積立・貯蓄
furniture_monthly = furniture_monthly_contrib(settings, var_budgets)
furniture_start = resolve_sinking_start("家具家電", selected_month, settings)
furniture_fund = calc_sinking_fund(
    df_exp, furniture_monthly, selected_month, furniture_start, category="家具家電"
)

allowance_monthly = allowance_monthly_contrib(settings, var_budgets)  # ← ここを修正
allowance_start = resolve_sinking_start("お小遣い", selected_month, settings)
allowance_fund = calc_sinking_fund(
    df_exp, allowance_monthly, selected_month, allowance_start, group="お小遣い"
)

savings_info = calc_savings_reserve(selected_month, settings)

# 繰越・手元（積立残高・貯蓄確保を差し引いた自由資金）
carryover = calc_carryover(
    df_exp, df_inc, df_fix, selected_month, fixed_budgets, var_budgets, settings
)
free_now = free_cash_as_of(
    df_exp, df_inc, df_fix, selected_month, fixed_budgets, var_budgets, settings
)
available = free_now["free"]

budget_df = build_category_budget_df(m_exp, var_budgets, df_exp, selected_month, settings)
group_budget_df = build_group_budget_df(budget_df, allowance_fund)

# =========================================================
# KPI（総収入 / 総支出 / 総収支）
# =========================================================
st.subheader(f"{selected_month} の総合収支")

kpi1, kpi2, kpi3 = st.columns(3)
with kpi1:
    st.metric("総収入", yen(total_income))
with kpi2:
    st.metric(
        "総支出",
        yen(total_spend),
        delta=f"固定 {yen(total_fixed)} ＋ 変動 {yen(total_variable)}",
        delta_color="off",
    )
with kpi3:
    st.metric("総収支", yen(net_balance), delta="黒字" if net_balance >= 0 else "赤字")

sub1, sub2, sub3 = st.columns(3)
sub1.metric("繰越残高（前月まで）", yen(carryover))
sub2.metric("手元見込み", yen(available))
sub3.metric(
    "確保済み（積立＋貯蓄）",
    yen(free_now["reserved_total"]),
    delta=f"家具 {yen(free_now['reserved_furniture'])} / 小遣い {yen(free_now['reserved_allowance'])} / 貯蓄 {yen(free_now['reserved_savings'])}",
    delta_color="off",
)
st.caption(
    "繰越残高・手元見込みは、総収支の累計から「家具家電積立残高」「お小遣い積立残高」「月次貯蓄の確保額」を差し引いた自由に使える金額です。"
)

# 貯蓄目標（ダッシュボード）
savings_goal = int(settings.get("貯蓄目標", 0))
cum_savings = int(savings_info["reserved"])
st.markdown("#### 貯蓄")
goal_note = str(settings.get("貯蓄目標メモ", "") or "")
s1, s2 = st.columns(2)
s1.metric("月次貯蓄額", yen(savings_info["monthly"]))
s2.metric("累計貯蓄（確保）", yen(cum_savings), delta=f"{savings_info['months']}ヶ月分")
if savings_goal > 0:
    progress = min(max(cum_savings / savings_goal, 0.0), 1.0)
    remain = max(savings_goal - cum_savings, 0)
    st.progress(
        progress,
        text=f"{yen(cum_savings)} / {yen(savings_goal)}（{progress * 100:.1f}%）"
        + (f"  — {goal_note}" if goal_note else ""),
    )
    st.caption(
        f"目標まであと {yen(remain)}" if remain > 0 else "目標達成です。設定タブで目標額・月次貯蓄額を変更できます。"
    )
else:
    st.caption("貯蓄目標が未設定です。設定タブで目標額と月次貯蓄額を入力してください。")

# 積立カード（家具家電・お小遣い）
st.markdown("#### 積立残高")
col_f, col_a = st.columns(2)

with col_f:
    st.metric(
        label="家具家電",
        value=yen(furniture_fund["balance"]),
        delta=f"月次: {yen(furniture_fund['monthly'])}",
        delta_color="off",
    )
    if furniture_fund["balance"] < 0:
        st.error("※家具家電の積立残高がマイナス（予算超過）です")

with col_a:
    st.metric(
        label="お小遣い",
        value=yen(allowance_fund["balance"]),
        delta=f"月次: {yen(allowance_fund['monthly'])}",
        delta_color="off",
    )
    if allowance_fund["balance"] < 0:
        st.error("※お小遣いの積立残高がマイナス（予算超過）です")

# 超過警告
over_normal = budget_df[(budget_df["運用"] == "通常") & (budget_df["使用率(%)"] > 100)]
over_fund = budget_df[(budget_df["運用"] == "積立") & (budget_df["残予算"] < 0)]
if not over_normal.empty:
    labels = ", ".join(
        f"{row['カテゴリ']}（{row['使用率(%)']}%）" for _, row in over_normal.iterrows()
    )
    st.warning(f"予算超過カテゴリ: {labels}")
if not over_fund.empty:
    # お小遣いは区分でまとめて1回だけ表示
    seen = set()
    parts = []
    for _, row in over_fund.iterrows():
        key = row["区分"] if row["区分"] in SINKING_FUND_GROUPS else row["カテゴリ"]
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{key}（残高 {yen(row['残予算'])}）")
    st.warning(f"積立残高マイナス: {', '.join(parts)}")

st.divider()

# =========================================================
# 区分別予実
# =========================================================
st.markdown("#### 区分別 予実サマリー")
st.caption("使用率の色: 青(0%) → 緑 → 黄 → 橙 → 赤(超過)。残予算がマイナスの行は赤文字です。")
col_g_tbl, col_g_fig = st.columns([1, 1])
with col_g_tbl:
    st.dataframe(style_budget_table(group_budget_df), use_container_width=True, hide_index=True)
with col_g_fig:
    st.plotly_chart(usage_bar_chart(group_budget_df, "区分", height=200), use_container_width=True)

st.divider()

# =========================================================
# タブ
# =========================================================
tab_exp, tab_fix, tab_inc, tab_trend, tab_year, tab_settings = st.tabs(
    ["変動費", "固定費", "収入", "月次トレンド", "年次サマリー", "設定"]
)

# ----- 変動費 -----
with tab_exp:
    st.markdown("#### カテゴリ別 予実")
    st.caption(
        "積立（家具家電・お小遣い）: 残予算＝積立残高、使用率＝累計支出÷累計積立。"
        "お小遣いの残予算は区分全体で共通です。"
    )
    col_c_tbl, col_c_fig = st.columns([1.3, 1])
    with col_c_tbl:
        st.dataframe(
            style_budget_table(
                budget_df[["区分", "カテゴリ", "運用", "予算", "実績", "残予算", "使用率(%)"]]
            ),
            use_container_width=True,
            hide_index=True,
            height=580,
        )
    with col_c_fig:
        st.plotly_chart(
            usage_bar_chart(budget_df, "カテゴリ", title="カテゴリ別 使用率", height=450),
            use_container_width=True,
        )

    st.markdown("#### カテゴリ別 金額グラフ")
    g1, g2 = st.columns(2)
    with g1:
        fig_amt = px.bar(
            budget_df,
            x="実績",
            y="カテゴリ",
            color="区分",
            orientation="h",
            text="実績",
            category_orders={"区分": GROUP_ORDER, "カテゴリ": ALL_CATEGORIES},
            color_discrete_sequence=["#3498DB", "#9B59B6", "#1ABC9C"],
            title="今月の実績額",
        )
        fig_amt.update_traces(texttemplate="¥%{text:,.0f}", textposition="outside")
        fig_amt.update_layout(
            height=420,
            margin=dict(l=10, r=40, t=40, b=10),
            yaxis={"categoryorder": "array", "categoryarray": ALL_CATEGORIES},
        )
        st.plotly_chart(fig_amt, use_container_width=True)
    with g2:
        pie_src = budget_df[budget_df["実績"] > 0]
        if pie_src.empty:
            st.info("今月の変動費実績がまだないため、円グラフを表示できません。")
        else:
            fig_pie_cat = px.pie(
                pie_src,
                names="カテゴリ",
                values="実績",
                hole=0.35,
                title="カテゴリ別 構成比",
            )
            fig_pie_cat.update_layout(height=420, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig_pie_cat, use_container_width=True)

    st.markdown("#### 今月の変動費明細")
    if m_exp.empty:
        st.info("今月の変動費はまだありません。「＋ 支出」から登録してください。")
    else:
        filter_cols = st.columns([1, 2.5])
        with filter_cols[0]:
            filter_group = st.multiselect("区分で絞り込み", GROUP_ORDER, default=GROUP_ORDER)
        with filter_cols[1]:
            filter_pay = st.multiselect("支払い方法", PAYMENT_METHODS, default=PAYMENT_METHODS)

        view = m_exp[m_exp["区分"].isin(filter_group) & m_exp["支払い方法"].isin(filter_pay)].copy()
        view = view.sort_values("日付", ascending=False)
        orig_indices = view.index

        if view.empty:
            st.info("条件に一致する明細がありません。")
        else:
            display_df = view.copy()
            display_df["削除"] = False
            # 区分は表示のみ（カテゴリ変更時に自動同期）
            edit_cols = ["日付", "区分", "カテゴリ", "内容", "金額", "支払い方法", "備考", "削除"]

            edited = st.data_editor(
                display_df[edit_cols],
                column_config={
                    "日付": st.column_config.DateColumn("日付"),
                    "区分": st.column_config.TextColumn("区分", disabled=True),
                    "カテゴリ": st.column_config.SelectboxColumn("カテゴリ", options=ALL_CATEGORIES),
                    "内容": st.column_config.TextColumn("内容"),
                    "金額": st.column_config.NumberColumn("金額", min_value=0, format="¥%d"),
                    "支払い方法": st.column_config.SelectboxColumn("支払い方法", options=PAYMENT_METHODS),
                    "備考": st.column_config.TextColumn("備考"),
                    "削除": st.column_config.CheckboxColumn("削除", default=False),
                },
                hide_index=True,
                use_container_width=True,
                key="exp_editor",
            )

            new_exp, changed = apply_edits(
                st.session_state.df_exp,
                edited,
                orig_indices,
                ["日付", "カテゴリ", "内容", "金額", "支払い方法", "備考"],
                sync_category_group=True,
            )
            if changed:
                st.session_state.df_exp = new_exp
                save_all_data()
                st.toast("支出の変更を保存しました", icon="💾")
                st.rerun()

            selected_positions = [i for i, flag in enumerate(edited["削除"].tolist()) if flag]
            if selected_positions:
                if st.button(f"選択した {len(selected_positions)} 件の支出を削除", type="primary", key="del_exp"):
                    st.session_state.df_exp = delete_by_positions(
                        st.session_state.df_exp, orig_indices, selected_positions
                    )
                    save_all_data()
                    st.success(f"{len(selected_positions)} 件を削除しました")
                    st.rerun()

        # 支払い方法別集計
        if not m_exp.empty:
            st.markdown("##### 支払い方法別 内訳")
            pay_sum = (
                m_exp.groupby("支払い方法", as_index=False)["金額"]
                .sum()
                .sort_values("金額", ascending=False)
            )
            fig_pay = px.pie(pay_sum, names="支払い方法", values="金額", hole=0.4)
            fig_pay.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_pay, use_container_width=True)

# ----- 固定費 -----
with tab_fix:
    st.markdown(f"#### {selected_month} の固定費")
    if fix_is_virtual:
        st.caption("この月の固定費は未確定です。金額を編集すると標準目安額で初期登録されます。")
    else:
        st.caption("金額を直接編集すると即座に保存されます。")

    # 選択された月の固定費データを取得
    display_fix = df_fix[df_fix["集計月"] == selected_month].copy() if not df_fix.empty else pd.DataFrame()

    # 「項目」列が含まれていない場合の補完処理
    if "項目" not in display_fix.columns:
        display_fix = pd.DataFrame(columns=["集計月", "項目", "金額", "備考"])

    # 予算列の追加（安全な参照）
    display_fix["予算"] = display_fix["項目"].map(lambda x: int(fixed_budgets.get(x, 0))) if not display_fix.empty else []
    display_fix["差額"] = display_fix["予算"] - display_fix["金額"]

    fix_orig = current_fix_df.index if not fix_is_virtual else pd.Index([])

    edited_fix = st.data_editor(
        display_fix[["項目", "予算", "金額", "差額", "備考"]],
        column_config={
            "項目": st.column_config.TextColumn("固定費項目", disabled=True),
            "予算": st.column_config.NumberColumn("予算", format="¥%d", disabled=True),
            "金額": st.column_config.NumberColumn("今月の確定額", min_value=0, format="¥%d"),
            "差額": st.column_config.NumberColumn("予算−実績", format="¥%d", disabled=True),
            "備考": st.column_config.TextColumn("備考"),
        },
        hide_index=True,
        use_container_width=True,
        key="fix_editor",
    )

    # 変更検知（仮想 or 実データ）
    baseline_amount = display_fix["金額"].map(to_int_amount).tolist()
    baseline_note = display_fix["備考"].map(lambda x: clean_text(x)).tolist()
    new_amount = edited_fix["金額"].map(to_int_amount).tolist()
    new_note = edited_fix["備考"].map(lambda x: clean_text(x)).tolist()
    fix_changed = baseline_amount != new_amount or baseline_note != new_note

    if fix_changed:
        persist_payload = edited_fix[["項目", "金額", "備考"]].copy()
        persist_payload["集計月"] = selected_month
        persist_fixed_month(persist_payload, selected_month, fix_is_virtual, fix_orig if not fix_is_virtual else None)
        st.toast("固定費を更新しました", icon="💾")
        st.rerun()

    fix_budget_total = int(sum(fixed_budgets.values()))
    st.caption(f"固定費合計 {yen(total_fixed)} ／ 予算合計 {yen(fix_budget_total)}")

# ----- 収入 -----
with tab_inc:
    st.markdown(f"#### {selected_month} の収入明細")
    if m_inc.empty:
        st.info("今月の収入はまだありません。「＋ 収入」から登録してください。")
    else:
        view_inc = m_inc.sort_values("日付", ascending=False)
        inc_indices = view_inc.index
        display_inc = view_inc.copy()
        display_inc["削除"] = False

        edited_inc = st.data_editor(
            display_inc[["日付", "種別", "金額", "備考", "削除"]],
            column_config={
                "日付": st.column_config.DateColumn("日付"),
                "種別": st.column_config.SelectboxColumn("種別", options=INCOME_TYPES),
                "金額": st.column_config.NumberColumn("金額", min_value=0, format="¥%d"),
                "備考": st.column_config.TextColumn("備考"),
                "削除": st.column_config.CheckboxColumn("削除", default=False),
            },
            hide_index=True,
            use_container_width=True,
            key="inc_editor",
        )

        new_inc, inc_changed = apply_edits(
            st.session_state.df_inc,
            edited_inc,
            inc_indices,
            ["日付", "種別", "金額", "備考"],
        )
        if inc_changed:
            st.session_state.df_inc = new_inc
            save_all_data()
            st.toast("収入の変更を保存しました", icon="💾")
            st.rerun()

        selected_inc_pos = [i for i, flag in enumerate(edited_inc["削除"].tolist()) if flag]
        if selected_inc_pos:
            if st.button(f"選択した {len(selected_inc_pos)} 件の収入を削除", type="primary", key="del_inc"):
                st.session_state.df_inc = delete_by_positions(
                    st.session_state.df_inc, inc_indices, selected_inc_pos
                )
                save_all_data()
                st.success("収入データを削除しました")
                st.rerun()

# ----- 月次トレンド -----
with tab_trend:
    st.markdown("#### 月次トレンド")
    trend_df = build_monthly_trend(df_exp, df_inc, df_fix, fixed_budgets)
    if trend_df.empty:
        st.info("表示できる月次データがありません。")
    else:
        melt = trend_df.melt(id_vars="集計月", value_vars=["収入", "固定費", "変動費", "収支"], var_name="項目", value_name="金額")
        fig_trend = px.line(melt, x="集計月", y="金額", color="項目", markers=True)
        fig_trend.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="金額（円）")
        st.plotly_chart(fig_trend, use_container_width=True)

        st.dataframe(
            trend_df.style.format({"収入": "¥{:,.0f}", "固定費": "¥{:,.0f}", "変動費": "¥{:,.0f}", "収支": "¥{:,.0f}"}),
            use_container_width=True,
            hide_index=True,
        )

# ----- 年次サマリー -----
with tab_year:
    st.markdown("#### 年次サマリー")
    trend_df = build_monthly_trend(df_exp, df_inc, df_fix, fixed_budgets)
    if trend_df.empty:
        st.info("表示できるデータがありません。")
    else:
        trend_df = trend_df.copy()
        trend_df["年"] = trend_df["集計月"].str[:4]
        years = sorted(trend_df["年"].unique(), reverse=True)
        selected_year = st.selectbox("年を選択", years)

        year_df = trend_df[trend_df["年"] == selected_year].sort_values("集計月")
        y_income = int(year_df["収入"].sum())
        y_fixed = int(year_df["固定費"].sum())
        y_var = int(year_df["変動費"].sum())
        y_net = int(year_df["収支"].sum())

        y1, y2, y3, y4 = st.columns(4)
        y1.metric(f"{selected_year} 総収入", yen(y_income))
        y2.metric("総固定費", yen(y_fixed))
        y3.metric("総変動費", yen(y_var))
        y4.metric("年間収支", yen(y_net), delta="黒字" if y_net >= 0 else "赤字")

        fig_year = px.bar(
            year_df,
            x="集計月",
            y=["固定費", "変動費"],
            title=f"{selected_year}年 支出内訳（月別）",
            barmode="stack",
        )
        fig_year.add_scatter(x=year_df["集計月"], y=year_df["収入"], name="収入", mode="lines+markers")
        fig_year.update_layout(height=400, margin=dict(l=10, r=10, t=40, b=10), yaxis_title="金額（円）")
        st.plotly_chart(fig_year, use_container_width=True)

        # カテゴリ別年間変動費
        if not df_exp.empty:
            year_exp = df_exp[df_exp["集計月"].str.startswith(selected_year)]
            if not year_exp.empty:
                cat_year = (
                    year_exp.groupby(["区分", "カテゴリ"], as_index=False)["金額"]
                    .sum()
                    .sort_values("金額", ascending=False)
                )
                st.markdown("##### カテゴリ別 年間変動費")
                st.dataframe(
                    cat_year.style.format({"金額": "¥{:,.0f}"}),
                    use_container_width=True,
                    hide_index=True,
                )

                fig_cat_year = px.bar(
                    cat_year,
                    x="金額",
                    y="カテゴリ",
                    color="区分",
                    orientation="h",
                    category_orders={"区分": GROUP_ORDER, "カテゴリ": ALL_CATEGORIES},
                )
                fig_cat_year.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig_cat_year, use_container_width=True)

# ----- 設定 -----
with tab_settings:
    st.markdown("#### 変動費予算")
    st.caption("カテゴリごとの月次予算を編集できます。")
    budget_edit_rows = []
    for group in GROUP_ORDER:
        for cat in CATEGORIES[group]:
            budget_edit_rows.append({"区分": group, "カテゴリ": cat, "予算": int(var_budgets.get(cat, 0))})
    budget_edit_df = pd.DataFrame(budget_edit_rows)

    edited_budgets = st.data_editor(
        budget_edit_df,
        column_config={
            "区分": st.column_config.TextColumn("区分", disabled=True),
            "カテゴリ": st.column_config.TextColumn("カテゴリ", disabled=True),
            "予算": st.column_config.NumberColumn("月次予算", min_value=0, step=1000, format="¥%d"),
        },
        hide_index=True,
        use_container_width=True,
        height=580,
        key="var_budget_editor",
    )
    if st.button("変動費予算を保存", type="primary", key="save_var_budget"):
        new_budgets = {row["カテゴリ"]: to_int_amount(row["予算"]) for _, row in edited_budgets.iterrows()}
        # 欠けたカテゴリはデフォルト維持
        for cat in ALL_CATEGORIES:
            new_budgets.setdefault(cat, DEFAULT_VAR_BUDGET[cat])
        st.session_state.var_budgets = new_budgets
        save_var_budgets(new_budgets)
        st.success("変動費予算を保存しました")
        st.rerun()

    st.divider()
    st.markdown("#### 固定費の標準予算")
    st.caption("新しい月の初期値として使われる標準額です。")
    fixed_edit_rows = [{"項目": k, "予算": int(v)} for k, v in fixed_budgets.items()]
    # デフォルトにない項目も残す
    for k, v in DEFAULT_FIXED_BUDGET.items():
        if k not in fixed_budgets:
            fixed_edit_rows.append({"項目": k, "予算": v})
    fixed_edit_df = pd.DataFrame(fixed_edit_rows).drop_duplicates(subset=["項目"], keep="first")

    edited_fixed_budget = st.data_editor(
        fixed_edit_df,
        column_config={
            "項目": st.column_config.TextColumn("項目", disabled=True),
            "予算": st.column_config.NumberColumn("標準予算", min_value=0, step=500, format="¥%d"),
        },
        hide_index=True,
        use_container_width=True,
        key="fixed_budget_editor",
    )
    if st.button("固定費予算を保存", type="primary", key="save_fixed_budget"):
        new_fixed = {row["項目"]: to_int_amount(row["予算"]) for _, row in edited_fixed_budget.iterrows()}
        st.session_state.fixed_budgets = new_fixed
        save_fixed_budgets(new_fixed)
        st.success("固定費の標準予算を保存しました")
        st.rerun()

    st.divider()
    st.markdown("#### 積立の開始月")
    st.caption(
        "家具家電はカテゴリ予算、お小遣いは趣味＋交際費＋娯楽雑費の合計を毎月積み立てます。"
        "支出登録するだけで残高から減ります。"
    )

    current_furn_monthly = furniture_monthly_contrib(settings, var_budgets)
    current_allow_monthly = allowance_monthly_contrib(settings, var_budgets)

    c_start1, c_start2 = st.columns(2)
    with c_start1:
        st.markdown("##### 家具家電")
        sinking_amt_f = st.number_input(
            "家具家電 月次積立額（円）",
            min_value=0,
            step=1000,
            value=current_furn_monthly,
            key="sinking_amt_furniture",
        )
        sinking_start_f = st.text_input(
            "家具家電 開始月（YYYY-MM）",
            value=str(settings.get("積立開始月_家具家電", "") or selected_month),
            key="sinking_start_furniture",
        )
        st.caption(f"月次積立: {yen(furniture_monthly)}")
    with c_start2:
        st.markdown("##### お小遣い")
        sinking_amt_a = st.number_input(
            "お小遣い 月次積立額（円）",
            min_value=0,
            step=1000,
            value=current_allow_monthly,
            key="sinking_amt_allowance",
        )
        sinking_start_a = st.text_input(
            "お小遣い 開始月（YYYY-MM）",
            value=str(settings.get("積立開始月_お小遣い", "") or selected_month),
            key="sinking_start_allowance",
        )
        st.caption(f"月次積立: {yen(allowance_monthly)}")

    if st.button("積立設定を保存", type="primary", key="save_sinking"):
        errors = []
        vals = {}
        for label, raw in [("家具家電", sinking_start_f), ("お小遣い", sinking_start_a)]:
            start_val = raw.strip()[:7]
            try:
                datetime.strptime(start_val + "-01", "%Y-%m-%d")
                vals[f"積立開始月_{label}"] = start_val
            except ValueError:
                errors.append(label)

        if errors:
            st.error(f"YYYY-MM 形式で入力してください: {', '.join(errors)}")
        else:
            # 1. 開始月と積立額をsettings.jsonに独立して保存
            vals["積立額_お小遣い"] = int(sinking_amt_a)
            vals["積立額_家具家電"] = int(sinking_amt_f)

            st.session_state.settings = {**st.session_state.settings, **vals}
            save_settings(st.session_state.settings)

            st.success("積立設定を保存しました")
            st.rerun()

    st.divider()
    st.markdown("#### 貯蓄設定")
    st.caption(
        "毎月この金額を「貯蓄」として手元から確保します。"
        "繰越残高・手元見込みからは差し引かれ、下の目標進捗に加算されます。"
    )
    sav_monthly = st.number_input(
        "月次貯蓄額（円）",
        min_value=0,
        step=5000,
        value=int(settings.get("月次貯蓄額", 30000)),
        key="monthly_savings_input",
    )
    sav_start = st.text_input(
        "貯蓄開始月（YYYY-MM）",
        value=str(settings.get("貯蓄開始月", "") or selected_month),
        key="savings_start_input",
    )
    goal = st.number_input(
        "貯蓄目標金額（円）",
        min_value=0,
        step=10000,
        value=int(settings.get("貯蓄目標", 500000)),
        key="savings_goal_input",
    )
    goal_note_input = st.text_input(
        "メモ",
        value=str(settings.get("貯蓄目標メモ", "")),
        placeholder="例: 旅行・緊急資金",
        key="savings_goal_note",
    )
    progress_set = min(cum_savings / goal, 1.0) if goal > 0 else 0.0
    st.progress(progress_set, text=f"進捗 {yen(cum_savings)} / {yen(goal)}（{progress_set * 100:.1f}%）")
    remain_set = max(goal - cum_savings, 0)
    st.caption(f"目標まであと {yen(remain_set)}" if remain_set > 0 else "目標達成です")

    if st.button("貯蓄設定を保存", type="primary", key="save_savings"):
        start_val = sav_start.strip()[:7]
        try:
            datetime.strptime(start_val + "-01", "%Y-%m-%d")
        except ValueError:
            st.error("貯蓄開始月は YYYY-MM 形式で入力してください（例: 2026-01）")
        else:
            st.session_state.settings = {
                **st.session_state.settings,
                "月次貯蓄額": int(sav_monthly),
                "貯蓄開始月": start_val,
                "貯蓄目標": int(goal),
                "貯蓄目標メモ": goal_note_input,
            }
            save_settings(st.session_state.settings)
            st.success("貯蓄設定を保存しました")
            st.rerun()

    st.divider()
    st.markdown("#### データバックアップ")
    if st.button("CSV を再読み込み（ディスクから）"):
        st.session_state.df_exp, st.session_state.df_inc, st.session_state.df_fix = load_data()
        st.session_state.var_budgets = load_var_budgets()
        st.session_state.fixed_budgets = load_fixed_budgets()
        st.session_state.settings = load_settings()
        st.success("再読み込みしました")
        st.rerun()

    # ダウンロード
    dl1, dl2, dl3 = st.columns(3)
    with dl1:
        st.download_button(
            "変動費 CSV",
            data=st.session_state.df_exp.to_csv(index=False).encode("utf-8-sig"),
            file_name="household_expenses.csv",
            mime="text/csv",
        )
    with dl2:
        st.download_button(
            "収入 CSV",
            data=st.session_state.df_inc.to_csv(index=False).encode("utf-8-sig"),
            file_name="household_income.csv",
            mime="text/csv",
        )
    with dl3:
        st.download_button(
            "固定費 CSV",
            data=st.session_state.df_fix.to_csv(index=False).encode("utf-8-sig"),
            file_name="household_fixed_monthly.csv",
            mime="text/csv",
        )

    # 設定タブや一時領域に配置
    if st.button("既存のCSVデータをSupabaseへ移行"):
        if os.path.exists("household_expenses.csv"):
            df_old = pd.read_csv("household_expenses.csv")
            for _, row in df_old.iterrows():
                save_expense_item(
                    date_str=str(row["日付"]),
                    group=str(row["区分"]),
                    category=str(row["カテゴリ"]),
                    content=str(row.get("内容", "-")),
                    amount=int(row["金額"]),
                    pay_method=str(row.get("支払い方法", "現金")),
                    month_key=str(row["集計月"]),
                    note=str(row.get("備考", "-"))
                )
            st.success("CSVデータの移行が完了しました！")
