import os
import random
import sys

import orjson
import toml
from dotenv import load_dotenv
from tqdm import tqdm

# 添加项目根目录到 Python 路径
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.append(root_path)

# 加载配置文件
config_path = os.path.join(root_path, "config", "bot_config.toml")
with open(config_path, encoding="utf-8") as f:
    config = toml.load(f)

# 现在可以导入src模块
from individuality.not_using.scene import get_scene_by_factor, PERSONALITY_SCENES  # noqa E402
from individuality.not_using.questionnaire import FACTOR_DESCRIPTIONS
from individuality.not_using.offline_llm import LLMRequestOff

# 加载环境变量
env_path = os.path.join(root_path, ".env")
if os.path.exists(env_path):
    print(f"从 {env_path} 加载环境变量")
    load_dotenv(env_path)
else:
    print(f"未找到环境变量文件: {env_path}")
    print("将使用默认配置")


def adapt_scene(scene: str) -> str:
    personality_core = config["personality"]["personality_core"]
    personality_side = config["personality"]["personality_side"]
    personality_side = random.choice(personality_side)
    identitys = config["identity"]["identity"]
    identity = random.choice(identitys)

    """
    根据config中的属性，改编场景使其更适合当前角色

    Args:
        scene: 原始场景描述

    Returns:
        str: 改编后的场景描述
    """
    try:
        prompt = f"""
这是一个参与人格测评的角色形象:
- 昵称: {config["bot"]["nickname"]}
- 性别: {config["identity"]["gender"]}
- 年龄: {config["identity"]["age"]}岁
- 外貌: {config["identity"]["appearance"]}
- 性格核心: {personality_core}
- 性格侧面: {personality_side}
- 身份细节: {identity}

请根据上述形象，改编以下场景，在测评中，用户将根据该场景给出上述角色形象的反应:
{scene}
保持场景的本质不变，但最好贴近生活且具体，并且让它更适合这个角色。
改编后的场景应该自然、连贯，并考虑角色的年龄、身份和性格特点。只返回改编后的场景描述，不要包含其他说明。注意{config["bot"]["nickname"]}是面对这个场景的人，而不是场景的其他人。场景中不会有其描述，
现在，请你给出改编后的场景描述
"""

        llm = LLMRequestOff(model_name=config["model"]["llm_normal"]["name"])
        adapted_scene, _ = llm.generate_response(prompt)

        # 检查返回的场景是否为空或错误信息
        if not adapted_scene or "错误" in adapted_scene or "失败" in adapted_scene:
            print("场景改编失败，将使用原始场景")
            return scene

        return adapted_scene
    except Exception as e:
        print(f"场景改编过程出错：{e!s}，将使用原始场景")
        return scene


class PersonalityEvaluatorDirect:
    def __init__(self):
        self.personality_traits = {"开放性": 0, "严谨性": 0, "外向性": 0, "宜人性": 0, "神经质": 0}
        self.scenarios = []
        self.final_scores: dict[str, float] = {"开放性": 0, "严谨性": 0, "外向性": 0, "宜人性": 0, "神经质": 0}
        self.dimension_counts = dict.fromkeys(self.final_scores, 0)

        # 为每个人格特质获取对应的场景
        for trait in PERSONALITY_SCENES:
            scenes = get_scene_by_factor(trait)
            if not scenes:
                continue

            # 从每个维度选择3个场景
            import random

            scene_keys = list(scenes.keys())
            selected_scenes = random.sample(scene_keys, min(3, len(scene_keys)))

            for scene_key in selected_scenes:
                scene = scenes[scene_key]

                # 为每个场景添加评估维度
                # 主维度是当前特质，次维度随机选择一个其他特质
                other_traits = [t for t in PERSONALITY_SCENES if t != trait]
                secondary_trait = random.choice(other_traits)

                self.scenarios.append(
                    {"场景": scene["scenario"], "评估维度": [trait, secondary_trait], "场景编号": scene_key}
                )

        self.llm = LLMRequestOff()

    def evaluate_response(self, scenario: str, response: str, dimensions: list[str]) -> dict[str, float]:
        """
        使用 DeepSeek AI 评估用户对特定场景的反应
        """
        # 构建维度描述
        dimension_descriptions = [f"- {dim}：{desc}" for dim in dimensions if (desc := FACTOR_DESCRIPTIONS.get(dim, ""))]

        dimensions_text = "\n".join(dimension_descriptions)

        prompt = f"""请根据以下场景和用户描述，评估用户在大五人格模型中的相关维度得分（1-6分）。

场景描述：
{scenario}

用户回应：
{response}

需要评估的维度说明：
{dimensions_text}

请按照以下格式输出评估结果（仅输出JSON格式）：
{{
    "{dimensions[0]}": 分数,
    "{dimensions[1]}": 分数
}}

评分标准：
1 = 非常不符合该维度特征
2 = 比较不符合该维度特征
3 = 有点不符合该维度特征
4 = 有点符合该维度特征
5 = 比较符合该维度特征
6 = 非常符合该维度特征

请根据用户的回应，结合场景和维度说明进行评分。确保分数在1-6之间，并给出合理的评估。"""

        try:
            ai_response, _ = self.llm.generate_response(prompt)
            # 尝试从AI响应中提取JSON部分
            start_idx = ai_response.find("{")
            end_idx = ai_response.rfind("}") + 1
            if start_idx != -1 and end_idx != 0:
                json_str = ai_response[start_idx:end_idx]
                scores = orjson.loads(json_str)
                # 确保所有分数在1-6之间
                return {k: max(1, min(6, float(v))) for k, v in scores.items()}
            else:
                print("AI响应格式不正确，使用默认评分")
                return dict.fromkeys(dimensions, 3.5)
        except Exception as e:
            print(f"评估过程出错：{e!s}")
            return dict.fromkeys(dimensions, 3.5)

    def run_evaluation(self):
        """
        运行整个评估过程
        """
        print(f"欢迎使用{config['bot']['nickname']}形象创建程序！")
        print("接下来，将给您呈现一系列有关您bot的场景（共15个）。")
        print("请想象您的bot在以下场景下会做什么，并描述您的bot的反应。")
        print("每个场景都会进行不同方面的评估。")
        print("\n角色基本信息：")
        print(f"- 昵称：{config['bot']['nickname']}")
        print(f"- 性格核心：{config['personality']['personality_core']}")
        print(f"- 性格侧面：{config['personality']['personality_side']}")
        print(f"- 身份细节：{config['identity']['identity']}")
        print("\n准备好了吗？按回车键开始...")
        input()

        total_scenarios = len(self.scenarios)
        progress_bar = tqdm(
            total=total_scenarios,
            desc="场景进度",
            ncols=100,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        )

        for _i, scenario_data in enumerate(self.scenarios, 1):
            # print(f"\n{'-' * 20} 场景 {i}/{total_scenarios} - {scenario_data['场景编号']} {'-' * 20}")

            # 改编场景，使其更适合当前角色
            print(f"{config['bot']['nickname']}祈祷中...")
            adapted_scene = adapt_scene(scenario_data["场景"])
            scenario_data["改编场景"] = adapted_scene

            print(adapted_scene)
            print(f"\n请描述{config['bot']['nickname']}在这种情况下会如何反应：")
            response = input().strip()

            if not response:
                print("反应描述不能为空！")
                continue

            print("\n正在评估您的描述...")
            scores = self.evaluate_response(adapted_scene, response, scenario_data["评估维度"])

            # 更新最终分数
            for dimension, score in scores.items():
                self.final_scores[dimension] += score
                self.dimension_counts[dimension] += 1

            print("\n当前评估结果：")
            print("-" * 30)
            for dimension, score in scores.items():
                print(f"{dimension}: {score}/6")

            # 更新进度条
            progress_bar.update(1)

            # if i < total_scenarios:
            # print("\n按回车键继续下一个场景...")
            # input()

        progress_bar.close()

        # 计算平均分
        for dimension in self.final_scores:
            if self.dimension_counts[dimension] > 0:
                self.final_scores[dimension] = round(self.final_scores[dimension] / self.dimension_counts[dimension], 2)

        print("\n" + "=" * 50)
        print(f"  {config['bot']['nickname']}的人格特征评估结果  ".center(50))
        print("=" * 50)
        for trait, score in self.final_scores.items():
            print(f"{trait}: {score}/6".ljust(20) + f"测试场景数：{self.dimension_counts[trait]}".rjust(30))
        print("=" * 50)

        # 返回评估结果
        return self.get_result()

    def get_result(self):
        """
        获取评估结果
        """
        return {
            "final_scores": self.final_scores,
            "dimension_counts": self.dimension_counts,
            "scenarios": self.scenarios,
            "bot_info": {
                "nickname": config["bot"]["nickname"],
                "gender": config["identity"]["gender"],
                "age": config["identity"]["age"],
                "height": config["identity"]["height"],
                "weight": config["identity"]["weight"],
                "appearance": config["identity"]["appearance"],
                "personality_core": config["personality"]["personality_core"],
                "personality_side": config["personality"]["personality_side"],
                "identity": config["identity"]["identity"],
            },
        }


def main():
    evaluator = PersonalityEvaluatorDirect()
    result = evaluator.run_evaluation()

    # 准备简化的结果数据
    simplified_result = {
        "openness": round(result["final_scores"]["开放性"] / 6, 1),  # 转换为0-1范围
        "conscientiousness": round(result["final_scores"]["严谨性"] / 6, 1),
        "extraversion": round(result["final_scores"]["外向性"] / 6, 1),
        "agreeableness": round(result["final_scores"]["宜人性"] / 6, 1),
        "neuroticism": round(result["final_scores"]["神经质"] / 6, 1),
        "bot_nickname": config["bot"]["nickname"],
    }

    # 确保目录存在
    save_dir = os.path.join(root_path, "data", "personality")
    os.makedirs(save_dir, exist_ok=True)

    # 创建文件名，替换可能的非法字符
    bot_name = config["bot"]["nickname"]
    # 替换Windows文件名中不允许的字符
    for char in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
        bot_name = bot_name.replace(char, "_")

    file_name = f"{bot_name}_personality.per"
    save_path = os.path.join(save_dir, file_name)

    # 保存简化的结果
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(orjson.dumps(simplified_result, option=orjson.OPT_INDENT_2).decode("utf-8"))

    print(f"\n结果已保存到 {save_path}")

    # 同时保存完整结果到results目录
    os.makedirs("results", exist_ok=True)
    with open("results/personality_result.json", "w", encoding="utf-8") as f:
        f.write(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode("utf-8"))


if __name__ == "__main__":
    main()
