#!/usr/bin/env python3
"""
nutrition_calc.py - 营养计算与数据持久化引擎
功能: init-profile, log-meal, daily-summary, query-remaining,
      update-weight, list-today, undo-last, show-profile, test
"""
import argparse, json, os, sys
from datetime import datetime, date
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def ensure_data_dir(d):
    p = Path(d); p.mkdir(parents=True, exist_ok=True); return p

def load_json(fp, default=None):
    if default is None: default = {}
    if fp.exists():
        try:
            with open(fp,'r',encoding='utf-8') as f: return json.load(f)
        except Exception: return default
    return default

def save_json(fp, data):
    tmp = fp.with_suffix('.tmp')
    with open(tmp,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
    tmp.replace(fp)

ACTIVITY_MULT = {"久坐":1.2,"sedentary":1.2,"轻度活动":1.375,"light":1.375,
    "中度活动":1.55,"moderate":1.55,"重度活动":1.725,"heavy":1.725}

def normalize_goal(goal):
    g = (goal or "").strip()
    mapping = {
        "lose_fat": "减脂", "慢速减脂": "慢速减脂", "减脂": "减脂",
        "build_muscle": "增肌", "增肌": "增肌",
        "maintain": "维持体重", "balanced": "维持体重", "维持体重": "维持体重", "均衡营养": "维持体重",
    }
    return mapping.get(g, "维持体重")


def _energy_ratio_by_goal(goal, weight, activity):
    g = normalize_goal(goal)
    low_activity = activity in ("久坐", "sedentary", "轻度活动", "light")

    if g == "减脂":
        if weight < 60: return -0.10
        if weight < 80: return -0.13
        return -0.16
    if g == "慢速减脂":
        if weight < 60: return -0.07
        if weight < 80: return -0.09
        return -0.11
    if g == "增肌":
        return 0.08 if low_activity else 0.12
    return 0.0


def _macro_targets_by_goal(goal, weight):
    g = normalize_goal(goal)
    if g in ("减脂", "慢速减脂"):
        return {"protein_g_per_kg": 1.8, "fat_g_per_kg_min": 0.8, "carb_g_per_kg_min": 2.0}
    if g == "增肌":
        return {"protein_g_per_kg": 1.8, "fat_g_per_kg_min": 0.7, "carb_g_per_kg_min": 3.0}
    return {"protein_g_per_kg": 1.4, "fat_g_per_kg_min": 0.8, "carb_g_per_kg_min": 2.5}

def calc_bmr(gender,age,h,w):
    return 10*w+6.25*h-5*age+(5 if gender in("男","male","m") else -161)

def calc_tdee(bmr,act): return bmr*ACTIVITY_MULT.get(act,1.375)

def calc_targets(tdee, goal, weight, bmr, activity):
    ratio = _energy_ratio_by_goal(goal, weight, activity)
    # 采用按个体规模调整的热量策略，并遵循不低于 BMR 的安全约束。
    raw_cal = tdee * (1 + ratio)
    cal = max(bmr, raw_cal)

    macro = _macro_targets_by_goal(goal, weight)
    protein = weight * macro["protein_g_per_kg"]
    fat = max(weight * macro["fat_g_per_kg_min"], (cal * 0.22) / 9)
    carbs = (cal - protein * 4 - fat * 9) / 4

    if carbs < macro["carb_g_per_kg_min"] * weight:
        carbs = macro["carb_g_per_kg_min"] * weight
        fat = max(weight * 0.6, (cal - protein * 4 - carbs * 4) / 9)

    if fat < weight * 0.6:
        fat = weight * 0.6
        carbs = max(0, (cal - protein * 4 - fat * 9) / 4)

    return {
        "calories": round(cal),
        "protein": round(max(0, protein)),
        "carbs": round(max(0, carbs)),
        "fat": round(max(0, fat)),
        "fiber": 25,
        "sodium": 2300,
    }

def cmd_init_profile(a):
    dd=ensure_data_dir(a.data_dir); pp=dd/"user_profile.json"
    bmr=calc_bmr(a.gender,a.age,a.height,a.weight)
    tdee=calc_tdee(bmr,a.activity); tgt=calc_targets(tdee,a.goal,a.weight,bmr,a.activity)
    prof={"gender":a.gender,"age":a.age,"height":a.height,"weight":a.weight,
          "activity":a.activity,"goal":a.goal,"bmr":round(bmr),"tdee":round(tdee),
          "daily_targets":tgt,"created_at":datetime.now().isoformat(),
          "updated_at":datetime.now().isoformat(),"weight_history":[]}
    save_json(pp,prof)
    print(json.dumps({"status":"success","message":"用户档案已创建",
        "profile":{"bmr":prof["bmr"],"tdee":prof["tdee"],"daily_targets":tgt}},ensure_ascii=False,indent=2))

def cmd_log_meal(a):
    dd=ensure_data_dir(a.data_dir); lp=dd/"daily_log.json"
    log=load_json(lp,{"records":[]})
    try: foods=json.loads(a.foods)
    except json.JSONDecodeError as e:
        print(json.dumps({"status":"error","message":f"JSON解析失败:{e}"},ensure_ascii=False)); sys.exit(1)
    mt={"calories":0,"protein":0,"carbs":0,"fat":0,"fiber":0,"sodium":0}
    for f in foods:
        for k in mt: mt[k]+=f.get(k,0)
    mt={k:round(v,1) for k,v in mt.items()}
    today=a.date or date.today().isoformat()
    rec={"id":f"{today}_{a.meal_type}_{datetime.now().strftime('%H%M%S')}",
         "date":today,"meal_type":a.meal_type,"foods":foods,"totals":mt,
         "logged_at":datetime.now().isoformat()}
    log["records"].append(rec); save_json(lp,log)
    dt={k:0 for k in mt}
    for r in log["records"]:
        if r["date"]==today:
            for k in dt: dt[k]+=r["totals"].get(k,0)
    dt={k:round(v,1) for k,v in dt.items()}
    prof=load_json(dd/"user_profile.json"); tgt=prof.get("daily_targets",{})
    print(json.dumps({"status":"success","message":"饮食已记录","record_id":rec["id"],
        "meal_totals":mt,"daily_totals":dt,"daily_targets":tgt},ensure_ascii=False,indent=2))

def cmd_daily_summary(a):
    dd=ensure_data_dir(a.data_dir)
    log=load_json(dd/"daily_log.json",{"records":[]})
    prof=load_json(dd/"user_profile.json"); tgt=prof.get("daily_targets",{})
    td=a.date or date.today().isoformat()
    recs=[r for r in log["records"] if r["date"]==td]
    if not recs:
        print(json.dumps({"status":"no_data","message":f"{td}没有记录","date":td},ensure_ascii=False,indent=2)); return
    meals={}; dt={"calories":0,"protein":0,"carbs":0,"fat":0,"fiber":0,"sodium":0}
    mn={"breakfast":"早餐","lunch":"午餐","dinner":"晚餐","snack":"加餐"}
    for r in recs:
        m=r["meal_type"]
        if m not in meals: meals[m]={"name":mn.get(m,m),"calories":0,"foods":[]}
        meals[m]["calories"]+=r["totals"].get("calories",0)
        meals[m]["foods"].extend([f["name"] for f in r["foods"]])
        for k in dt: dt[k]+=r["totals"].get(k,0)
    dt={k:round(v,1) for k,v in dt.items()}
    ns={}
    for k in ["calories","protein","carbs","fat","fiber"]:
        t=tgt.get(k,0); act=dt.get(k,0)
        pct=round(act/t*100) if t>0 else 0
        ns[k]={"actual":act,"target":t,"percentage":pct,"status":"normal" if 80<=pct<=120 else("low" if pct<80 else "high")}
    sc=100
    for v in ns.values():
        d=abs(v["percentage"]-100)
        if d>30: sc-=15
        elif d>20: sc-=10
        elif d>10: sc-=5
    print(json.dumps({"status":"success","date":td,"meals":meals,"daily_totals":dt,
        "daily_targets":tgt,"nutrient_status":ns,"score":max(0,sc)},ensure_ascii=False,indent=2))

def cmd_query_remaining(a):
    dd=ensure_data_dir(a.data_dir)
    log=load_json(dd/"daily_log.json",{"records":[]})
    prof=load_json(dd/"user_profile.json"); tgt=prof.get("daily_targets",{})
    today=a.date or date.today().isoformat()
    dt={k:0 for k in ["calories","protein","carbs","fat","fiber","sodium"]}
    for r in log["records"]:
        if r["date"]==today:
            for k in dt: dt[k]+=r["totals"].get(k,0)
    rem={k:round(max(0,tgt.get(k,0)-v),1) for k,v in dt.items()}
    print(json.dumps({"status":"success","date":today,"consumed":{k:round(v,1) for k,v in dt.items()},
        "remaining":rem,"targets":tgt},ensure_ascii=False,indent=2))

def cmd_update_weight(a):
    dd=ensure_data_dir(a.data_dir); pp=dd/"user_profile.json"
    p=load_json(pp)
    if not p: print(json.dumps({"status":"error","message":"档案不存在"},ensure_ascii=False)); sys.exit(1)
    old=p.get("weight",0); p["weight"]=a.weight
    bmr=calc_bmr(p["gender"],p["age"],p["height"],a.weight)
    tdee=calc_tdee(bmr,p["activity"]); tgt=calc_targets(tdee,p["goal"],a.weight,bmr,p["activity"])
    p.update({"bmr":round(bmr),"tdee":round(tdee),"daily_targets":tgt,"updated_at":datetime.now().isoformat()})
    p.setdefault("weight_history",[]).append({"date":date.today().isoformat(),"weight":a.weight})
    save_json(pp,p)
    print(json.dumps({"status":"success","message":f"体重更新:{old}→{a.weight}kg",
        "new_targets":tgt},ensure_ascii=False,indent=2))

def cmd_list_today(a):
    dd=ensure_data_dir(a.data_dir)
    log=load_json(dd/"daily_log.json",{"records":[]})
    td=a.date or date.today().isoformat()
    recs=[r for r in log["records"] if r["date"]==td]
    print(json.dumps({"status":"success","date":td,"record_count":len(recs),
        "records":recs},ensure_ascii=False,indent=2))

def cmd_undo_last(a):
    dd=ensure_data_dir(a.data_dir); lp=dd/"daily_log.json"
    log=load_json(lp,{"records":[]})
    if not log["records"]:
        print(json.dumps({"status":"error","message":"没有可撤销的记录"},ensure_ascii=False)); return
    rm=log["records"].pop(); save_json(lp,log)
    print(json.dumps({"status":"success","message":"已撤销","removed":{"id":rm["id"],
        "foods":[f["name"] for f in rm["foods"]],"calories":rm["totals"]["calories"]}},ensure_ascii=False,indent=2))

def cmd_show_profile(a):
    dd=ensure_data_dir(a.data_dir); p=load_json(dd/"user_profile.json")
    if not p: print(json.dumps({"status":"error","message":"档案不存在"},ensure_ascii=False)); return
    print(json.dumps({"status":"success","profile":p},ensure_ascii=False,indent=2))

def cmd_test(a):
    print("自检测试")
    bmr=calc_bmr("男",30,175,70); tdee=calc_tdee(bmr,"轻度活动"); tgt=calc_targets(tdee,"减脂",70,bmr,"轻度活动")
    print(f"  BMR:{round(bmr)}kcal TDEE:{round(tdee)}kcal 目标:{tgt['calories']}kcal")
    print(f"  蛋白质:{tgt['protein']}g 碳水:{tgt['carbs']}g 脂肪:{tgt['fat']}g")
    assert tgt["calories"]>1200,"热量计算异常"
    print("所有测试通过")

def main():
    pa=argparse.ArgumentParser(description="健康饮食营养计算引擎")
    sp=pa.add_subparsers(dest="command")
    p1=sp.add_parser("init-profile")
    p1.add_argument("--gender",required=True); p1.add_argument("--age",type=int,required=True)
    p1.add_argument("--height",type=float,required=True); p1.add_argument("--weight",type=float,required=True)
    p1.add_argument("--activity",required=True); p1.add_argument("--goal",required=True)
    p1.add_argument("--data-dir",required=True)
    p2=sp.add_parser("log-meal")
    p2.add_argument("--meal-type",required=True,choices=["breakfast","lunch","dinner","snack"])
    p2.add_argument("--foods",required=True); p2.add_argument("--date",default=None)
    p2.add_argument("--data-dir",required=True)
    p3=sp.add_parser("daily-summary"); p3.add_argument("--date",default=None); p3.add_argument("--data-dir",required=True)
    p4=sp.add_parser("query-remaining"); p4.add_argument("--date",default=None); p4.add_argument("--data-dir",required=True)
    p5=sp.add_parser("update-weight"); p5.add_argument("--weight",type=float,required=True); p5.add_argument("--data-dir",required=True)
    p6=sp.add_parser("list-today"); p6.add_argument("--date",default=None); p6.add_argument("--data-dir",required=True)
    p7=sp.add_parser("undo-last"); p7.add_argument("--data-dir",required=True)
    p8=sp.add_parser("show-profile"); p8.add_argument("--data-dir",required=True)
    p9=sp.add_parser("test"); p9.add_argument("--data-dir",default="./test_data")
    args=pa.parse_args()
    cmds={"init-profile":cmd_init_profile,"log-meal":cmd_log_meal,"daily-summary":cmd_daily_summary,
          "query-remaining":cmd_query_remaining,"update-weight":cmd_update_weight,"list-today":cmd_list_today,
          "undo-last":cmd_undo_last,"show-profile":cmd_show_profile,"test":cmd_test}
    if args.command in cmds: cmds[args.command](args)
    else: pa.print_help()

if __name__=="__main__": main()
