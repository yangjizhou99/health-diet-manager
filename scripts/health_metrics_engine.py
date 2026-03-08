import os
import sys
import glob
import json
import pandas as pd
from pathlib import Path
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
                print(f"[Engine WARN] 加载心率文件失败 {f}: {e}", file=sys.stderr)
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
            except Exception as e:
                print(f"[Engine WARN] 加载睡眠文件失败 {f}: {e}", file=sys.stderr)
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
                if '日期' in df.columns and '体重' in df.columns and '体脂率' in df.columns and '时间' in df.columns:
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime', '体重'])
                    df_list.append(df)
            except Exception as e:
                print(f"[Engine WARN] 加载体重文件失败 {f}: {e}", file=sys.stderr)
        if not df_list:
            return pd.DataFrame()
        df_all = pd.concat(df_list, ignore_index=True)
        return df_all.sort_values('Datetime').reset_index(drop=True)

    def load_steps(self):
        """加载步数数据，标记数据来源用于多设备去重"""
        steps_dir = os.path.join(self.base_dir, "健康同步 步数")
        files = glob.glob(os.path.join(steps_dir, "*.csv"))
        df_list = []
        for f in files:
            try:
                df = pd.read_csv(f)
                if '日期' in df.columns and '步数' in df.columns and '时间' in df.columns:
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime', '步数'])
                    # 从文件名提取来源标识 (如 "Huawei Health", "Health Connect")
                    fname = os.path.basename(f)
                    if 'Health Connect' in fname:
                        source = 'HealthConnect'
                    elif 'Huawei Health' in fname:
                        source = 'HuaweiHealth'
                    else:
                        source = fname
                    df['source'] = source
                    df_list.append(df[["Datetime", "步数", "source"]])
            except Exception as e:
                print(f"[Engine WARN] 加载步数文件失败 {f}: {e}", file=sys.stderr)
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
                if '日期' in df.columns and '总消耗' in df.columns and '时间' in df.columns:
                    date_time_str = df['日期'].astype(str).str.split(' ').str[0] + ' ' + df['时间'].astype(str)
                    df['Datetime'] = pd.to_datetime(date_time_str, format='%Y.%m.%d %H:%M:%S', errors='coerce')
                    df = df.dropna(subset=['Datetime'])
                    df_list.append(df)
            except Exception as e:
                print(f"[Engine WARN] 加载能量文件失败 {f}: {e}", file=sys.stderr)
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
    def __init__(self, height_cm=None):
        self.height_m = (float(height_cm) / 100.0) if height_cm else None

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
                
                # 骨骼肌/脂肪比：用于趋势参考，不等同于医学定义的 SMI。
                fat_mass = weight * (body_fat_pct / 100)
                muscle_fat_ratio = round(skeletal_muscle / fat_mass, 2) if fat_mass > 0 else 0

                # 真实 SMI 需要四肢骨骼肌量(ASM)与身高，缺失时不输出数值。
                asm = latest_record.get('四肢骨骼肌量', latest_record.get('四肢骨骼肌', None))
                smi_kg_m2 = None
                if asm is not None and self.height_m and self.height_m > 0:
                    try:
                        smi_kg_m2 = round(float(asm) / (self.height_m ** 2), 2)
                    except Exception:
                        smi_kg_m2 = None
                
                reports[str(date)] = {
                    "weight_kg": round(weight, 2),
                    "body_fat_pct": round(body_fat_pct, 1),
                    "skeletal_muscle_kg": round(skeletal_muscle, 2),
                    "bmr_kcal": round(bmr, 0),
                    "muscle_fat_ratio": muscle_fat_ratio,
                    "smi_kg_m2": smi_kg_m2,
                    # 兼容历史字段，后续展示层不再将该字段标注为 SMI。
                    "smi_ratio": muscle_fat_ratio,
                }
            except Exception as e:
                pass
        return reports

class ActivityAnalyzer:
    """日常步数分布数据计算 (支持多设备时间序列去重)"""

    @staticmethod
    def _build_dedup_step_series(group):
        has_source = 'source' in group.columns
        sources = group['source'].unique() if has_source else []
        if not has_source or len(sources) <= 1:
            return group.groupby('Datetime')['步数'].max().resample('1min').sum().fillna(0)

        per_source = {}
        for src in sources:
            src_data = group[group['source'] == src][['Datetime', '步数']]
            src_data = src_data.groupby('Datetime')['步数'].max()
            per_source[src] = src_data.resample('1min').sum()

        all_idx = per_source[sources[0]].index
        for src in sources[1:]:
            all_idx = all_idx.union(per_source[src].index)

        aligned = {src: s.reindex(all_idx, fill_value=0) for src, s in per_source.items()}
        merged = pd.DataFrame(aligned)
        return merged.max(axis=1).fillna(0)

    def analyze(self, steps_df):
        if steps_df.empty:
            return {"error": "无步数数据"}
            
        steps_df['Date'] = steps_df['Datetime'].dt.date
        reports = {}
        for date, group in steps_df.groupby('Date'):
            dedup_1min = self._build_dedup_step_series(group)
            total_steps = int(dedup_1min.sum())
            resampled_3h = dedup_1min.resample('3h').sum()
            sedentary_blocks = int((resampled_3h < 100).sum())

            resampled_1min = dedup_1min
            fast_walks = []
            in_walk = False
            walk_start = None
            current_walk_steps = 0
            current_walk_mins = 0
            
            for time, steps in resampled_1min.items():
                if steps >= 100:
                    if not in_walk:
                        in_walk = True
                        walk_start = time
                    current_walk_steps += steps
                    current_walk_mins += 1
                else:
                    if in_walk:
                        if current_walk_mins >= 5:
                            end_time = time - pd.Timedelta(minutes=1)
                            fast_walks.append({
                                "start": walk_start.strftime("%Y-%m-%d %H:%M:%S"),
                                "end": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                                "duration_minutes": current_walk_mins,
                                "total_steps": int(current_walk_steps),
                                "max_steps_per_min": int(resampled_1min[walk_start:end_time].max())
                            })
                        in_walk = False
                        walk_start = None
                        current_walk_steps = 0
                        current_walk_mins = 0
                        
            if in_walk and current_walk_mins >= 5:
                end_time = resampled_1min.index[-1]
                fast_walks.append({
                    "start": walk_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_minutes": current_walk_mins,
                    "total_steps": int(current_walk_steps),
                    "max_steps_per_min": int(resampled_1min[walk_start:end_time].max())
                })
            
            reports[str(date)] = {
                "total_steps": total_steps,
                "sedentary_3h_blocks_count": sedentary_blocks,
                "fast_walks": fast_walks
            }
        return reports

class EnergyAnalyzer:
    """能耗审计纯数据计算 (支持基于心率的卡路里回退推算)"""
    def __init__(self, age=30, is_male=True, height=175):
        self.age = age
        self.height = height
        self.gender_factor = 1 if is_male else 0

    def _estimate_active_from_hr(self, hr_day_df, weight):
        """基于分钟级心率积分估计活动消耗，并返回数据质量指标。"""
        hr_resampled = hr_day_df.set_index('Datetime')[['心率']].resample('1min').mean().ffill(limit=5)
        active_hr_pts = hr_resampled[hr_resampled['心率'] > 90]

        estimated_active_kcals = 0.0
        if not active_hr_pts.empty:
            for hr in active_hr_pts['心率']:
                if self.gender_factor == 1:
                    cal_per_min = (-55.0969 + (0.6309 * hr) + (0.1988 * weight) + (0.2017 * self.age)) / 4.184
                else:
                    cal_per_min = (-20.4022 + (0.4472 * hr) - (0.1263 * weight) + (0.074 * self.age)) / 4.184
                if cal_per_min > 0:
                    estimated_active_kcals += cal_per_min

        return {
            "active_burn_kcal": round(estimated_active_kcals, 1),
            "minutes_total": int(len(hr_resampled)),
            "minutes_active": int(len(active_hr_pts)),
        }

    @staticmethod
    def _confidence_from_quality(minutes_total, minutes_active, has_weight):
        score = 0.45
        if minutes_total >= 720:
            score += 0.25
        elif minutes_total >= 360:
            score += 0.15
        elif minutes_total >= 180:
            score += 0.05

        if minutes_active >= 30:
            score += 0.20
        elif minutes_active >= 10:
            score += 0.10

        if has_weight:
            score += 0.10

        score = max(0.0, min(0.99, score))
        if score >= 0.80:
            return score, "high", 0.12
        if score >= 0.60:
            return score, "medium", 0.20
        return score, "low", 0.30

    def analyze(self, energy_df, hr_df, weight_df, allow_hr_fallback=True):
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
            external_tdee = None  # 保留外部设备提供的原始 TDEE
            method = "external_device"
            confidence_score = 0.95
            confidence_label = "high"
            assumptions = []
            active_burn_low = 0.0
            active_burn_high = 0.0
            tdee_low = 0.0
            tdee_high = 0.0
            
            # 首先尝试从 external energy df 读取
            if not energy_df.empty and date_str in energy_df['Date'].astype(str).values:
                group = energy_df[energy_df['Date'].astype(str) == date_str]
                latest_record = group.sort_values('Datetime').iloc[-1]
                active_burn = float(latest_record.get('活动消耗', 0))
                resting_burn = float(latest_record.get('静息消耗', 0))
                external_tdee = float(latest_record.get('总消耗', 0))
                tdee = external_tdee

            # 【核心逻辑】：如果外部同步的活动卡路里为 0，且当天有心率数据，启用 Keytel 公式回退推算
            if allow_hr_fallback and active_burn == 0 and date_str in daily_hr:
                weight = daily_weights.get(date_str, 75.0)  # 取当天体重，没有则用75kg
                hr_day_df = daily_hr[date_str]

                estimation = self._estimate_active_from_hr(hr_day_df, weight)
                active_burn = estimation["active_burn_kcal"]
                source_active = "estimated_from_hr"
                method = "keytel_hr_fallback"
                has_weight = date_str in daily_weights
                confidence_score, confidence_label, span_ratio = self._confidence_from_quality(
                    estimation["minutes_total"], estimation["minutes_active"], has_weight
                )

                assumptions = [
                    "仅将心率>90bpm分钟计为活动积分",
                    "分钟级重采样并前向填充最多5分钟",
                    "当日体重缺失时采用75kg默认体重",
                ]
                
                # 如果没有 external resting_burn， 用 Mifflin-St Jeor 估算基础代谢
                if resting_burn == 0:
                     if self.gender_factor == 1:
                         resting_burn = (10 * weight) + 6.25 * self.height - (5 * self.age) + 5
                     else:
                         resting_burn = (10 * weight) + 6.25 * self.height - (5 * self.age) - 161
                
                tdee = active_burn + resting_burn

                active_burn_low = round(max(0.0, active_burn * (1 - span_ratio)), 1)
                active_burn_high = round(active_burn * (1 + span_ratio), 1)
                tdee_low = round(max(0.0, resting_burn + active_burn_low), 1)
                tdee_high = round(resting_burn + active_burn_high, 1)
            else:
                active_burn_low = round(active_burn, 1)
                active_burn_high = round(active_burn, 1)
                tdee_low = round(tdee, 1)
                tdee_high = round(tdee, 1)

            # NEAT = 设备报告的总消耗 - 静息消耗 - 活动消耗 (仅外部数据源有差值)
            neat = round(max(0, external_tdee - resting_burn - active_burn), 1) if external_tdee is not None else 0.0
            reports[date_str] = {
                "active_burn_kcal": round(active_burn, 1),
                "active_burn_kcal_low": active_burn_low,
                "active_burn_kcal_high": active_burn_high,
                "resting_burn_kcal": round(resting_burn, 1),
                "tdee_kcal": round(tdee, 1),
                "tdee_kcal_low": tdee_low,
                "tdee_kcal_high": tdee_high,
                "neat_estimate_kcal": neat,
                "active_burn_source": source_active,
                "active_burn_method": method,
                "active_burn_confidence_score": round(confidence_score, 2),
                "active_burn_confidence_label": confidence_label,
                "active_burn_assumptions": assumptions,
            }
            
        return reports

def _load_user_profile(data_dir=None):
    """尝试从 data 目录加载用户档案以获取年龄、性别和身高"""
    default = {"age": 30, "is_male": True, "height": 175}
    if data_dir is None:
        return default
    profile_path = Path(data_dir) / "user_profile.json"
    if profile_path.exists():
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                prof = json.load(f)
            age = int(prof.get("age", 30))
            gender = prof.get("gender", "男")
            is_male = gender in ("男", "male", "m")
            height = float(prof.get("height", 175))
            return {"age": age, "is_male": is_male, "height": height}
        except Exception:
            pass
    return default

def _parse_date_arg(value, field_name):
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD, got: {value}") from exc


def _filter_datetime_df(df, start_date=None, end_date=None):
    if df.empty or "Datetime" not in df.columns:
        return df
    result = df
    if start_date is not None:
        result = result[result["Datetime"].dt.date >= start_date]
    if end_date is not None:
        result = result[result["Datetime"].dt.date <= end_date]
    return result.reset_index(drop=True)


def _filter_date_df(df, start_date=None, end_date=None):
    if df.empty or "Date" not in df.columns:
        return df
    result = df
    if start_date is not None:
        result = result[result["Date"] >= start_date]
    if end_date is not None:
        result = result[result["Date"] <= end_date]
    return result.reset_index(drop=True)


def generate_health_report(extracted_dir, data_dir=None, start_date=None, end_date=None, allow_estimated_energy=True):
    """引擎入口: 统筹分析并生成结构化报告 (纯数据版)"""
    print(f"[Engine] 加载数据目录: {extracted_dir}")
    parser = HealthDataParser(extracted_dir)
    
    # 从用户档案读取年龄、性别和身高，不再硬编码
    user_info = _load_user_profile(data_dir)
    age = user_info["age"]
    is_male = user_info["is_male"]
    height = user_info["height"]
    print(f"[Engine] 使用用户参数: age={age}, is_male={is_male}, height={height}")

    start = _parse_date_arg(start_date, "start_date")
    end = _parse_date_arg(end_date, "end_date")
    if start and end and start > end:
        raise ValueError(f"start_date cannot be after end_date: {start} > {end}")

    hr_df = _filter_datetime_df(parser.load_heart_rate(), start, end)
    sleep_df = _filter_date_df(parser.load_sleep(), start, end)
    weight_df = _filter_datetime_df(parser.load_weight(), start, end)
    steps_df = _filter_datetime_df(parser.load_steps(), start, end)
    energy_df = _filter_datetime_df(parser.load_energy(), start, end)

    hr_report = HeartRateAnalyzer(age=age).analyze(hr_df)
    sleep_report = SleepAnalyzer().analyze(sleep_df)
    body_comp_report = BodyCompositionAnalyzer(height_cm=height).analyze(weight_df)
    activity_report = ActivityAnalyzer().analyze(steps_df)
    energy_report = EnergyAnalyzer(age=age, is_male=is_male, height=height).analyze(
        energy_df,
        hr_df,
        weight_df,
        allow_hr_fallback=allow_estimated_energy,
    )
    
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
    test_dir = r"d:\gptWebapp\skills设计\健康信息例子\extracted"
    if os.path.exists(test_dir):
        report = generate_health_report(test_dir)
        print("\n\n=== 第三版健康数据引擎深度分析报告 ===\n")
        print(json.dumps(report, indent=4, ensure_ascii=False))
    else:
        print(f"找不到测试目录: {test_dir}")
