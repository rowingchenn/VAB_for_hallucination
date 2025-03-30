import os
import json

# 定义函数：拆分任务文件
def split_tasks(input_file, output_dir):
    # 检查输入文件是否存在
    if not os.path.exists(input_file):
        print(f"输入文件 {input_file} 不存在")
        return

    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    # 读取输入文件
    with open(input_file, "r") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"无法解析 JSON 文件：{e}")
            return

    # 遍历 JSON 数据中的任务
    for task in data:
        task_id = task.get("task_id")
        if task_id is None:
            print("任务缺少 task_id，跳过...")
            continue

        # 构造文件名
        file_name = f"{task_id}.json"
        file_path = os.path.join(output_dir, file_name)

        # 将任务写入到文件
        with open(file_path, "w") as output_file:
            json.dump(task, output_file, indent=2)
            print(f"任务 {task_id} 已保存到 {file_path}")

    print(f"所有任务已拆分并保存到目录：{output_dir}")

# 主程序
if __name__ == "__main__":
    # 输入整个 JSON 文件路径
    input_file = "hallucination_wa/test_hallucination_webarena.json"  # 替换为你的输入文件路径

    # 输出目录
    output_dir = "hallucination_wa/test_hallucination_webarena"  # 任务文件将保存在这个目录中

    # 调用函数
    split_tasks(input_file, output_dir)