"""
llm_parser.py
負責人: 梁偉航 (模組一 核心輸入層)

把使用者的一句話(手動輸入或語音轉文字)丟給 LLM,
用 Structured Output (response_schema) 強制模型回傳固定格式的 JSON,
避免自由文字造成後端解析崩潰。
"""

import os
import re
import json
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# 強制抓取當前檔案所在目錄下的 .env 檔案
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

from google import genai
from google.genai import types

# 強制載入專案根目錄的 .env 檔案
load_dotenv()

from google import genai
from google.genai import types
MODEL_NAME = "gemini-3.6-flash"

SYSTEM_PROMPT = """你是 PlantQuest 植栽系統的 AI 記帳解析器。
使用者會用口語化的一句中文描述他剛才做的園藝相關行為(可能同時包含買植物、買耗材介質、買工具、採收、換盆、釋出空盆等)。
你的任務是把這句話拆解成結構化事件,並以 JSON 格式回傳。

規則:
- 一句話可能包含多個事件,全部拆出來,不要遺漏也不要合併。
- 金額、重量沒提到就填 0,不要自己編造數字。
- 購買介質/水苔/泥炭土/肥料等消耗品, action_type 填 "supply_purchase", item_name 填品項名, quantity 填數量, unit 填單位(例如 包、公升、公斤)。
- 購買剪刀、翻堆叉、鏟子等設備, action_type 填 "tool_purchase"。
- 換盆、上盆使用盆器、釋出空盆洗淨等行為,一律歸類為 pot_transition:
  * 若是換盆/上盆/使用盆器: action 填 "use", quantity 填使用個數。
  * 若是釋出/洗淨/淘汰空盆回庫存: action 填 "release", quantity 填釋出個數。
- 只有純粹「買新盆器/花錢買盆子」才歸類為 container_purchase。
- 採收作物, action_type 填 "harvest", weight_g 填重量(克)。
- action_type 判斷不出來的花銷歸類到 generic_expense。
- companion_feedback 要像 RPG 遊戲管家的語氣,簡短、生動、正向(不超過 40 字)。
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "description": "拆解出的事件清單,一句話可能包含多個事件",
            "items": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": [
                            "plant_purchase",
                            "tool_purchase",
                            "container_purchase",
                            "pot_transition",
                            "supply_purchase",
                            "harvest",
                            "generic_expense",
                        ],
                    },
                    "action": {
                        "type": "string",
                        "description": "盆器流轉方向: 'use'(換盆/上盆在役) 或 'release'(釋出洗淨空盆),僅 pot_transition 使用",
                    },
                    "item_name": {"type": "string", "description": "品項名稱,例如 頂級智利水苔、4吋白方盆、鹿角蕨、修枝剪"},
                    "amount": {"type": "number", "description": "花費金額,沒提到就填 0"},
                    "quality_rating": {"type": "integer", "description": "1-5 的品質評分,沒提到就填 0"},
                    "market_stall_name": {"type": "string", "description": "花市或攤位名稱,例如 花市A3攤"},
                    "zone_name": {"type": "string", "description": "分區名稱,例如 陽台、室內燈養區"},
                    "crop_name": {"type": "string", "description": "採收作物名稱,僅 harvest 使用"},
                    "part": {"type": "string", "description": "採收部位,例如 葉、果實,僅 harvest 使用"},
                    "weight_g": {"type": "number", "description": "採收重量(公克),僅 harvest 使用"},
                    "unit": {"type": "string", "description": "單位,例如 包、個、公升、支"},
                    "quantity": {"type": "number", "description": "數量/個數,例如 1, 2"},
                },
                "required": ["action_type"],
            },
        },
        "companion_feedback": {
            "type": "string",
            "description": "用 RPG 園藝管家的口吻,對這次記帳給一句簡短生動的中文回饋(不超過 40 字)",
        },
    },
    "required": ["events", "companion_feedback"],
}


class ParseError(Exception):
    pass


def _clean_json_text(text: str) -> str:
    """清理可能包夾在 Markdown 中的 JSON 字串"""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def parse_dictation(raw_text: str, api_key: Optional[str] = None) -> dict:
    if not raw_text or not raw_text.strip():
        raise ParseError("輸入文字為空白")

    # 優先讀取傳入參數 -> 讀取環境變數 -> 兜底直接帶入你的金鑰
    key = api_key or os.getenv("GEMINI_API_KEY") or "AQ.Ab8RN6LO83OuBydWXh8DNIAK741W_J4RMjfkDm4A4INmnkcU7Q"
    if not key:
        raise ParseError("GEMINI_API_KEY 未設定,請至 https://aistudio.google.com/apikey 申請免費金鑰")
    client = genai.Client(api_key=key)

    last_error = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=raw_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    max_output_tokens=1024,
                ),
            )
            
            raw_res = response.text or ""
            clean_res = _clean_json_text(raw_res)
            result = json.loads(clean_res)
            
            if "events" not in result or "companion_feedback" not in result:
                raise ParseError("LLM 回傳缺少必要欄位")
            return result
        except json.JSONDecodeError as e:
            last_error = f"JSON解析錯誤: {e}, 原始內容: {getattr(response, 'text', '')}"
            continue
        except Exception as e:
            last_error = e
            continue

    raise ParseError(f"LLM 解析失敗(已重試): {last_error}")