"""
dispatcher.py
負責人: 梁偉航 (模組一 核心輸入層)

把 llm_parser 解析出的結構化事件,依 action_type 分派到 db_client 對應的寫入函式。
拆成獨立檔案是為了讓 test1.py 可以直接測試分派邏輯,不需要真的連 LLM 或資料庫。
"""

from typing import Optional
import db_client as db


def dispatch_event(event: dict, source: str, raw_text: str) -> dict:
    """
    處理單一事件,回傳處理結果摘要(方便 API 回應與除錯)。
    刻意不讓單一事件失敗擋住其他事件 -- 呼叫端(main.py)會逐一 try/except。
    """
    action_type = event.get("action_type")
    amount = event.get("amount") or 0

    if action_type == "plant_purchase":
        species = event.get("item_name") or "未命名植株"
        stall_name = event.get("market_stall_name")
        zone_name = event.get("zone_name") or "陽台"
        quality_rating = event.get("quality_rating") or None  # 0 代表「沒提到」，存 None 而不是假的 0 星

        plant_id = db.create_plant(
            species=species,
            acquired_cost=amount,
            zone_name=zone_name,
        )
        stall_id = db.find_or_create_market_stall(stall_name) if stall_name else None

        db.create_transaction(
            tx_type="plant_purchase", amount=amount, item_name=species,
            quality_rating=quality_rating, market_stall_id=stall_id,
            plant_id=plant_id, source=source, raw_text=raw_text,
        )
        return {
            "action_type": action_type,
            "plant_id": plant_id,
            "item_name": species,
            "amount": amount,
            "market_stall_name": stall_name,
            "zone_name": zone_name,
            "quality_rating": quality_rating,
            "status": "ok",
        }

    elif action_type == "tool_purchase":
        name = event.get("item_name") or "未命名工具"
        tool_id = db.create_tool(name=name, cost=amount)
        db.create_transaction(
            tx_type="tool_purchase", amount=amount, item_name=name,
            tool_id=tool_id, source=source, raw_text=raw_text,
        )
        return {
            "action_type": action_type,
            "tool_id": tool_id,
            "item_name": name,
            "amount": amount,
            "status": "ok",
        }

    elif action_type == "container_purchase":
        spec = event.get("item_name") or "未命名盆器"
        container_id = db.create_container(spec=spec, cost=amount)
        db.create_transaction(
            tx_type="container_purchase", amount=amount, item_name=spec,
            container_id=container_id, source=source, raw_text=raw_text,
        )
        return {
            "action_type": action_type,
            "container_id": container_id,
            "item_name": spec,
            "amount": amount,
            "status": "ok",
        }

    elif action_type in ("pot_transition", "repot", "container_use"):
        # 盆器流轉 / 換盆使用 / 空盆釋出
        item_name = event.get("item_name") or "4吋白方盆"
        qty = int(float(event.get("quantity") or 1.0))
        action_dir = event.get("action") or "use"

        # 真正修改 SQLite 資料庫內 Container 的狀態
        try:
            from database import SessionLocal
            import models3

            # 抓取規格關鍵字 (若品名含 4吋 或 方盆 則精準對齊，避免口語冗字比對失敗)
            kw = "4吋" if "4吋" in item_name else ("方盆" if "方盆" in item_name else item_name)

            with SessionLocal() as db_session:
                if action_dir == "use":
                    # 把空盆改為在役
                    availables = db_session.query(models3.Container).filter(
                        models3.Container.spec.like(f"%{kw}%"),
                        models3.Container.status == "空盆"
                    ).limit(qty).all()
                    for item in availables:
                        item.status = "在役"
                elif action_dir == "release":
                    # 把在役改為空盆
                    in_uses = db_session.query(models3.Container).filter(
                        models3.Container.spec.like(f"%{kw}%"),
                        models3.Container.status == "在役"
                    ).limit(qty).all()
                    for item in in_uses:
                        item.status = "空盆"
                db_session.commit()
        except Exception as e:
            print("更新盆器狀態失敗:", e)

        # 寫入統一帳本流水紀錄
        try:
            action_desc = "換盆/上盆在役" if action_dir == "use" else "洗淨釋出空盆"
            db.create_transaction(
                tx_type="pot_transition",
                amount=0,
                item_name=f"{action_desc}：{item_name} ({qty}個)",
                source=source,
                raw_text=raw_text,
            )
        except Exception:
            pass

        return {
            "action_type": "pot_transition",
            "item_name": item_name,
            "action": action_dir,
            "qty": qty,
            "status": "ok",
        }

    elif action_type == "supply_purchase":
        name = event.get("item_name") or "未命名耗材"
        unit = event.get("unit") or "unit"
        delta = event.get("quantity") or 0
        supply_id = db.upsert_supply(
            name=name,
            unit=unit,
            quantity_delta=delta,
        )
        db.create_transaction(
            tx_type="supply_purchase", amount=amount, item_name=name,
            supply_id=supply_id, source=source, raw_text=raw_text,
        )
        return {
            "action_type": action_type,
            "supply_id": supply_id,
            "item_name": name,
            "unit": unit,
            "quantity": delta,
            "amount": amount,
            "status": "ok",
        }

    elif action_type == "harvest":
        crop_name = event.get("crop_name") or event.get("item_name") or "未命名作物"
        part = event.get("part") or "葉片"
        weight_g = event.get("weight_g") or 0
        harvest_id = db.create_harvest(
            crop_name=crop_name,
            part=part,
            weight_g=weight_g,
        )
        db.create_transaction(
            tx_type="harvest_income",
            amount=0,
            item_name=f"{crop_name}（{weight_g}g）",
            source=source, raw_text=raw_text,
        )
        return {
            "action_type": action_type,
            "harvest_id": harvest_id,
            "item_name": crop_name,
            "part": part,
            "weight_g": weight_g,
            "status": "ok",
        }

    elif action_type == "generic_expense":
        name = event.get("item_name") or "未分類花銷"
        db.create_transaction(
            tx_type="generic_expense", amount=amount, item_name=name,
            source=source, raw_text=raw_text,
        )
        return {
            "action_type": action_type,
            "item_name": name,
            "amount": amount,
            "status": "ok",
        }

    else:
        return {"action_type": action_type, "status": "skipped", "reason": "未知的 action_type"}


def dispatch_all(events: list, source: str, raw_text: str) -> list:
    results = []
    for event in events:
        try:
            results.append(dispatch_event(event, source, raw_text))
        except Exception as e:
            results.append({
                "action_type": event.get("action_type"),
                "status": "error",
                "reason": str(e),
            })
    return results