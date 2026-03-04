import os
import glob
import pandas as pd
from datetime import datetime, timedelta

class HealthDataParser:
    """负责跨类别加载和清洗华为/HealthConnect导出的健康CSV数据"""
    def __init__(self, extracted_dir):
        self.base_dir = extracted_dir

    def load_heart_rate(self):
        """加载心率数据并按时间序列索引"""
        hr_dir = os.path.join(self.base_dir, "健康同步 心率")
        files = glob.glob(os.path.join(hr_dir, "*.csv"))
        df_list = []
        for f in files:
            # 跳过异常汇总小文件，找详情文件
            try:
                # 华为的心率有不同的表头，可能是 日期,时间,心率 或 直接Time, 心率
                df = pd.read_csv(f)
                if '日期' in df.columns and '时间' in df.columns and '心率' in df.columns:
                    # 组合成datetime 2026.03.04 00:04:00
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime', '心率'])
                    df = df[['Datetime', '心率']]
                    df_list.append(df)
            except Exception as e:
                pass
        if not df_list:
            return pd.DataFrame()
        df_all = pd.concat(df_list, ignore_index=True)
        df_all = df_all.sort_values('Datetime').reset_index(drop=True)
        return df_all

    def load_sleep(self):
        """加载睡眠分期数据"""
        sleep_dir = os.path.join(self.base_dir, "健康同步 睡眠")
        files = glob.glob(os.path.join(sleep_dir, "*.csv"))
        df_list = []
        for f in files:
            try:
                df = pd.read_csv(f)
                if '日期' in df.columns and '持续时间（以秒为单位）' in df.columns and '睡眠阶段' in df.columns:
                    # 获取日期作为归属
                    date_str = df['日期'].astype(str).str.split(' ').str[0]
                    df['Date'] = pd.to_datetime(date_str, format='%Y.%m.%d', errors='coerce').dt.date
                    df = df[['Date', '持续时间（以秒为单位）', '睡眠阶段']]
                    df_list.append(df)
            except:
                pass
        if not df_list:
            return pd.DataFrame()
        return pd.concat(df_list, ignore_index=True)

    def load_weight(self):
        """加载体重/体脂分期数据"""
        weight_dir = os.path.join(self.base_dir, "健康同步 体重")
        files = glob.glob(os.path.join(weight_dir, "*.csv"))
        df_list = []
        for f in files:
            try:
                df = pd.read_csv(f)
                if '日期' in df.columns and '体重' in df.columns and '体脂率' in df.columns:
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime', '体重'])
                    df_list.append(df)
            except:
                pass
        if not df_list:
            return pd.DataFrame()
        df_all = pd.concat(df_list, ignore_index=True)
        return df_all.sort_values('Datetime').reset_index(drop=True)

    def load_steps(self):
        """加载步数数据"""
        steps_dir = os.path.join(self.base_dir, "健康同步 步数")
        files = glob.glob(os.path.join(steps_dir, "*.csv"))
        df_list = []
        for f in files:
            try:
                df = pd.read_csv(f)
                if '日期' in df.columns and '步数' in df.columns:
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime', '步数'])
                    df_list.append(df[["Datetime", "步数"]])
            except:
                pass
        if not df_list:
            return pd.DataFrame()
        df_all = pd.concat(df_list, ignore_index=True)
        return df_all.sort_values('Datetime').reset_index(drop=True)

    def load_energy(self):
        """加载能量消耗数据"""
        energy_dir = os.path.join(self.base_dir, "健康同步 消耗能量")
        files = glob.glob(os.path.join(energy_dir, "*.csv"))
        df_list = []
        for f in files:
            try:
                df = pd.read_csv(f)
                if '日期' in df.columns and '总消耗' in df.columns:
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime'])
                    df_list.append(df)
            except:
                pass
        if not df_list:
            return pd.DataFrame()
        df_all = pd.concat(df_list, ignore_index=True)
        return df_all.sort_values('Datetime').reset_index(drop=True)

class HeartRateAnalyzer:
    """心率及隐性运动推算"""
    def __init__(self, age=30):
        self.age = age
        self.max_hr = 220 - age
        self.zone2_min = int(self.max_hr * 0.6)
        self.zone3_min = int(self.max_hr * 0.7)
        self.zone4_min = int(self.max_hr * 0.8)
        self.zone5_min = int(self.max_hr * 0.9)

    def analyze(self, hr_df):
        if hr_df.empty:
            return {"error": "无心率数据"}
        
        # 1. 基础指标
        hr_series = hr_df['心率']
        rhr = hr_series.quantile(0.05) # 用5%分位数代表静息心率，避开极低噪点
        peak_hr = hr_series.max()
        
        # 2. 隐性运动推算算法 (Inferring Exercise from HR)
        # 去除同一时间的重复记录 (取平均)
        hr_df = hr_df.groupby('Datetime')['心率'].mean().reset_index()
        # 寻找连续 > zone2_min 的波段
        hr_df = hr_df.set_index('Datetime')
        # 重采样到每分钟，向前填充缺失值（最多填5分钟）
        hr_resampled = hr_df.resample('1min').ffill(limit=5)
        
        workouts = []
        in_workout = False
        workout_start = None
        current_workout_hrs = []
        
        for time, row in hr_resampled.iterrows():
            hr = row['心率']
            if pd.isna(hr):
                continue
                
            if hr >= self.zone2_min:
                if not in_workout:
                    in_workout = True
                    workout_start = time
                current_workout_hrs.append(hr)
            else:
                if in_workout:
                    # 运动结束，判定是否是有效运动 (持续 >= 5 分钟)
                    duration_mins = len(current_workout_hrs)
                    if duration_mins >= 5:
                        avg_hr = sum(current_workout_hrs) / duration_mins
                        workouts.append({
                            "start": workout_start.strftime("%Y-%m-%d %H:%M:%S"),
                            "end": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "duration_minutes": duration_mins,
                            "avg_hr": round(avg_hr),
                            "peak_hr": max(current_workout_hrs)
                        })
                    
                    # 重置状态
                    in_workout = False
                    workout_start = None
                    current_workout_hrs = []
        
        # 处理跨越到最后的数据
        if in_workout and len(current_workout_hrs) >= 5:
            duration_mins = len(current_workout_hrs)
            workouts.append({
                "start": workout_start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": hr_resampled.index[-1].strftime("%Y-%m-%d %H:%M:%S"),
                "duration_minutes": duration_mins,
                "avg_hr": round(sum(current_workout_hrs) / duration_mins),
                "peak_hr": max(current_workout_hrs)
            })

        # 运动总耗时
        total_zone2_plus_mins = sum(w['duration_minutes'] for w in workouts)
        
        return {
            "baseline": {
                "estimated_rhr": int(rhr),
                "observed_peak_hr": int(peak_hr),
                "zonal_thresholds": {
                    "Zone2": [self.zone2_min, self.zone3_min - 1],
                    "Zone3": [self.zone3_min, self.zone4_min - 1]
                }
            },
            "inferred_workouts": workouts,
            "total_exercise_minutes_zone2_plus": int(total_zone2_plus_mins)
        }

class SleepAnalyzer:
    """睡眠架构分析"""
    def analyze(self, sleep_df):
        if sleep_df.empty:
            return {"error": "无睡眠数据"}
        
        # 按照日期汇总各个阶段的总秒数
        daily_summary = sleep_df.groupby(['Date', '睡眠阶段'])['持续时间（以秒为单位）'].sum().reset_index()
        
        reports = {}
        for date, group in daily_summary.groupby('Date'):
            total_seconds = group['持续时间（以秒为单位）'].sum()
            phases = dict(zip(group['睡眠阶段'], group['持续时间（以秒为单位）']))
            
            deep_sec = phases.get('deep', 0)
            rem_sec = phases.get('rem', 0)
            awake_sec = phases.get('awake', 0)
            light_sec = phases.get('light', 0)
            
            # 排除awake来计算真实睡眠时间
            actual_sleep_sec = total_seconds - awake_sec
            
            if actual_sleep_sec > 0:
                deep_ratio = deep_sec / actual_sleep_sec
                rem_ratio = rem_sec / actual_sleep_sec
                efficiency = actual_sleep_sec / total_seconds if total_seconds > 0 else 0
                
                reports[str(date)] = {
                    "total_sleep_hours": round(actual_sleep_sec / 3600, 2),
                    "deep_sleep_ratio": round(deep_ratio, 3),
                    "rem_ratio": round(rem_ratio, 3),
                    "sleep_efficiency": round(efficiency, 3),
                    "awake_interruptions_mins": round(awake_sec / 60, 1)
                }
                
        return reports

class BodyCompositionAnalyzer:
    """体重成分纯数据计算"""
    def analyze(self, weight_df):
        if weight_df.empty:
            return {"error": "无体脂数据"}
        
        weight_df['Date'] = weight_df['Datetime'].dt.date
        reports = {}
        for date, group in weight_df.groupby('Date'):
            latest_record = group.sort_values('Datetime').iloc[-1]
            try:
                weight = float(latest_record.get('体重', 0))
                body_fat_pct = float(latest_record.get('体脂率', 0))
                skeletal_muscle = float(latest_record.get('骨骼肌质量', 0))
                bmr = float(latest_record.get('基础代谢率', 0))
                
                # SMI 指数近似 (骨骼肌/体脂肪量)
                fat_mass = weight * (body_fat_pct / 100)
                smi_index = round(skeletal_muscle / fat_mass, 2) if fat_mass > 0 else 0
                
                reports[str(date)] = {
                    "weight_kg": round(weight, 2),
                    "body_fat_pct": round(body_fat_pct, 1),
                    "skeletal_muscle_kg": round(skeletal_muscle, 2),
                    "bmr_kcal": round(bmr, 0),
                    "smi_ratio": smi_index
                }
            except Exception as e:
                pass
        return reports

class ActivityAnalyzer:
    """日常步数分布数据计算"""
    def analyze(self, steps_df):
        if steps_df.empty:
            return {"error": "无步数数据"}
            
        steps_df['Date'] = steps_df['Datetime'].dt.date
        reports = {}
        for date, group in steps_df.groupby('Date'):
            total_steps = int(group['步数'].sum())
            
            # 计算久坐中断 (连续3小时步数 < 100)
            group = group.set_index('Datetime')
            resampled_3h = group['步数'].resample('3h').sum()
            sedentary_blocks = int((resampled_3h < 100).sum())
            
            reports[str(date)] = {
                "total_steps": total_steps,
                "sedentary_3h_blocks_count": sedentary_blocks
            }
        return reports

class EnergyAnalyzer:
    """能耗审计纯数据计算 (支持基于心率的卡路里回退推算)"""
    def __init__(self, age=30, is_male=True):
        self.age = age
        self.gender_factor = 1 if is_male else 0

    def analyze(self, energy_df, hr_df, weight_df):
        # 建立按天查询的体重字典 (默认 75kg)
        daily_weights = {}
        if not weight_df.empty:
            weight_df['Date'] = weight_df['Datetime'].dt.date
            for date, group in weight_df.groupby('Date'):
                daily_weights[str(date)] = float(group.sort_values('Datetime').iloc[-1].get('体重', 75.0))

        # 按天重组心率数据用于分钟级积分
        daily_hr = {}
        if not hr_df.empty:
            hr_df['Date'] = hr_df['Datetime'].dt.date
            for date, group in hr_df.groupby('Date'):
                # 过滤异常值为有效的心跳点
                valid_hr = group[group['心率'] > 40]
                daily_hr[str(date)] = valid_hr

        # 整理基础 external data 的日期范围
        all_dates = set()
        if not energy_df.empty:
             energy_df['Date'] = energy_df['Datetime'].dt.date
             all_dates.update(energy_df['Date'].astype(str).tolist())
        all_dates.update(daily_hr.keys())

        reports = {}
        for date_str in sorted(list(all_dates)):
            active_burn = 0.0
            resting_burn = 0.0
            tdee = 0.0
            source_active = "external" 
            
            # 首先尝试从 external energy df 读取
            if not energy_df.empty and date_str in energy_df['Date'].astype(str).values:
                group = energy_df[energy_df['Date'].astype(str) == date_str]
                latest_record = group.sort_values('Datetime').iloc[-1]
                active_burn = float(latest_record.get('活动消耗', 0))
                resting_burn = float(latest_record.get('静息消耗', 0))
                tdee = float(latest_record.get('总消耗', 0))

            # 【核心逻辑】：如果外部同步的活动卡路里为 0，且当天有心率数据，启用 Keytel 公式回退推算
            if active_burn == 0 and date_str in daily_hr:
                weight = daily_weights.get(date_str, 75.0)  # 取当天体重，没有则用75kg
                hr_day_df = daily_hr[date_str]
                
                # 设置静息判定线 (Zone 1 的上限附近，例如 90bpm)，只有高于此才算“活动”
                # 或者严格一点基于 Keytel 积分:
                estimated_active_kcals = 0.0
                
                # 需要用到心率差值，简单地将连续的高心率点(>90bpm)积分
                active_hr_pts = hr_day_df[hr_day_df['心率'] > 90]
                
                if not active_hr_pts.empty:
                    # 分别计算每个心率点的卡路里消耗率，然后加总（假设每条记录代表1分钟，考虑到重采样可做到精确）
                    # 对于有规律的时序，按照 Keytel:
                    # Cal/min = (-55.0969 + (0.6309 * HR) + (0.1988 * Wt_kg) + (0.2017 * Age)) / 4.184 (男性版)
                    # 男：Cal/min = (-55.0969 + (0.6309 * HR) + (0.1988 * Wt) + (0.2017 * Age))/4.184
                    # 女：Cal/min = (-20.4022 + (0.4472 * HR) - (0.1263 * Wt) + (0.074  * Age))/4.184
                    
                    for hr in active_hr_pts['心率']:
                        if self.gender_factor == 1:
                            cal_per_min = (-55.0969 + (0.6309 * hr) + (0.1988 * weight) + (0.2017 * self.age)) / 4.184
                        else:
                            cal_per_min = (-20.4022 + (0.4472 * hr) - (0.1263 * weight) + (0.074 * self.age)) / 4.184
                            
                        if cal_per_min > 0:
                            estimated_active_kcals += cal_per_min
                            
                active_burn = round(estimated_active_kcals, 1)
                source_active = "estimated_from_hr"
                
                # 如果没有 external resting_burn， 用 Mifflin-St Jeor 估算基础代谢
                if resting_burn == 0:
                     if self.gender_factor == 1:
                         resting_burn = (10 * weight) + 6.25 * 175 - (5 * self.age) + 5 # 假设身高175
                     else:
                         resting_burn = (10 * weight) + 6.25 * 165 - (5 * self.age) - 161
                
                tdee = active_burn + resting_burn

            reports[date_str] = {
                "active_burn_kcal": round(active_burn, 1),
                "resting_burn_kcal": round(resting_burn, 1),
                "tdee_kcal": round(tdee, 1),
                "neat_estimate_kcal": round(max(0, tdee - resting_burn - active_burn), 1),
                "active_burn_source": source_active
            }
            
        return reports

def generate_health_report(extracted_dir):
    """引擎入口: 统筹分析并生成结构化报告 (纯数据版)"""
    print(f"[Engine] 加载数据目录: {extracted_dir}")
    parser = HealthDataParser(extracted_dir)
    
    hr_df = parser.load_heart_rate()
    weight_df = parser.load_weight()
    energy_df = parser.load_energy()

    hr_report = HeartRateAnalyzer(age=30).analyze(hr_df)
    sleep_report = SleepAnalyzer().analyze(parser.load_sleep())
    body_comp_report = BodyCompositionAnalyzer().analyze(weight_df)
    activity_report = ActivityAnalyzer().analyze(parser.load_steps())
    energy_report = EnergyAnalyzer(age=30, is_male=True).analyze(energy_df, hr_df, weight_df)
    
    comprehensive_report = {
        "analysis_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {
            "cardiovascular_health": hr_report,
            "sleep_recovery": sleep_report,
            "body_composition": body_comp_report,
            "daily_activity": activity_report,
            "energy_expenditure": energy_report
        }
    }
    
    return comprehensive_report

if __name__ == "__main__":
    # Test Run
    import json
    test_dir = r"d:\gptWebapp\skills设计\健康信息例子\extracted"
    if os.path.exists(test_dir):
        report = generate_health_report(test_dir)
        print("\n\n=== 第三版健康数据引擎深度分析报告 ===\n")
        print(json.dumps(report, indent=4, ensure_ascii=False))
    else:
        print(f"找不到测试目录: {test_dir}")
