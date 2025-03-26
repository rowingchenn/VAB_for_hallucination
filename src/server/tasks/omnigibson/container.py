import docker
from docker.types import DeviceRequest
import os
import fcntl
import socket
import time
import random
import subprocess
from src.typings import (
    TaskSampleExecutionResult,
    TaskOutput,
    SampleIndex,
    AgentOutputStatus,
    SampleStatus,
)

ICD_PATH_1 = "/usr/share/vulkan/icd.d/nvidia_icd.json"
ICD_PATH_2 = "/etc/vulkan/icd.d/nvidia_icd.json"
LAYERS_PATH_1 = "/usr/share/vulkan/icd.d/nvidia_layers.json"
LAYERS_PATH_2 = "/usr/share/vulkan/implicit_layer.d/nvidia_layers.json"
LAYERS_PATH_3 = "/etc/vulkan/implicit_layer.d/nvidia_layers.json"
EGL_VENDOR_PATH = "/usr/share/glvnd/egl_vendor.d/10_nvidia.json"


class Container:

    available_ports = []  # [12000, 12001, 12002, 12003, 12004, 12005, 12006, 12007]
    available_devices = {}  # {"0":1, "1":1, "2":1, "3":1, "4":1, "5":1, "6":1, "7":1}
    output_dir = "outputs/omnigibson"
    data_dir = "data/omnigibson"
    vab_source_dir = ""
    modified_omnigibson_src = ""
    max_round = 100
    docker_image = ""
    use_apptainer = True  # 使用Apptainer而不是Docker
    sif_file_path = ""  # 指定的SIF文件路径

    def ensure_sif_image(self, docker_image):
        """确保存在对应的SIF镜像文件，如果不存在则从Docker镜像构建"""
        # 如果指定了SIF文件路径，优先使用
        if self.sif_file_path:
            if os.path.exists(self.sif_file_path):
                print(f"使用指定的SIF镜像文件: {self.sif_file_path}")
                return os.path.abspath(self.sif_file_path)  # 返回绝对路径
            else:
                print(
                    f"警告: 指定的SIF文件 {self.sif_file_path} 不存在，将尝试自动构建"
                )

        # 处理镜像名称，确保它是一个有效的文件名
        image_basename = os.path.basename(docker_image)

        # 使用当前工作目录构建完整路径，避免路径混淆
        current_dir = os.getcwd()
        sif_file = os.path.join(current_dir, f"{image_basename}.sif")
        sif_file = os.path.abspath(sif_file)  # 转换为绝对路径
        print(f"SIF文件完整路径: {sif_file}")

        # 检查SIF文件是否存在
        if os.path.exists(sif_file):
            print(f"SIF镜像文件 {sif_file} 已存在，直接使用")
            return sif_file

        # 检查是否已拉取Docker镜像
        try:
            # 构建SIF镜像
            print(f"从Docker镜像 {docker_image} 构建SIF镜像文件...")
            build_cmd = ["apptainer", "build", sif_file, f"docker://{docker_image}"]
            print(f"执行命令: {' '.join(build_cmd)}")
            result = subprocess.run(build_cmd, capture_output=True, text=True)

            if result.returncode != 0:
                print(f"构建失败，返回码: {result.returncode}")
                if result.stderr:
                    print(f"错误输出: {result.stderr}")
                if result.stdout:
                    print(f"标准输出: {result.stdout}")

                # 如果构建失败，尝试从本地Docker镜像构建
                print(f"从远程仓库构建失败，尝试从本地Docker镜像构建...")
                try:
                    # 检查是否安装了docker命令
                    check_docker = subprocess.run(
                        ["which", "docker"], capture_output=True, text=True
                    )
                    if check_docker.returncode != 0:
                        print(
                            "未检测到docker命令，无法从本地Docker镜像构建。请确保已经安装docker或者预先构建好SIF文件。"
                        )
                        raise Exception("Docker命令未安装，无法导出本地镜像")

                    # 首先将Docker镜像导出为tar文件
                    tmp_tar = f"{image_basename.replace(':', '_')}.tar"
                    export_cmd = ["docker", "save", "-o", tmp_tar, docker_image]
                    export_result = subprocess.run(
                        export_cmd, capture_output=True, text=True
                    )

                    if export_result.returncode != 0:
                        raise Exception(f"导出Docker镜像失败: {export_result.stderr}")

                    # 从tar文件构建SIF镜像
                    build_from_tar_cmd = [
                        "apptainer",
                        "build",
                        sif_file,
                        f"docker-archive:{tmp_tar}",
                    ]
                    tar_result = subprocess.run(
                        build_from_tar_cmd, capture_output=True, text=True
                    )

                    # 删除临时tar文件
                    if os.path.exists(tmp_tar):
                        os.remove(tmp_tar)

                    if tar_result.returncode != 0:
                        raise Exception(
                            f"从tar文件构建SIF镜像失败: {tar_result.stderr}"
                        )
                except Exception as docker_error:
                    print(f"从本地Docker镜像构建失败: {docker_error}")
                    print("尝试直接构建镜像，请确保您有合适的权限...")

                    # 如果所有方法都失败，给出清晰的错误信息
                    error_msg = f"""
无法创建SIF镜像文件。请尝试以下方法之一:
1. 确保您有网络访问权限并能够从Docker Hub拉取镜像
2. 预先使用以下命令手动构建SIF文件:
   apptainer build {sif_file} docker://{docker_image}
3. 如果您已经安装了Docker并拉取了相应镜像，可以尝试手动导出并构建:
   docker save -o {image_basename.replace(':', '_')}.tar {docker_image}
   apptainer build {sif_file} docker-archive:{image_basename.replace(':', '_')}.tar
4. 在配置文件中使用sif_file_path参数直接指定SIF文件路径
"""
                    raise Exception(error_msg)

            print(f"SIF镜像文件 {sif_file} 构建成功")
            return sif_file
        except Exception as e:
            raise Exception(f"构建SIF镜像失败: {str(e)}")

    def use_port(self):
        time.sleep(random.random())
        for port in self.available_ports:
            port_lock_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "tmp", f"port_{port}.lock"
            )
            try:
                port_lock_file = open(port_lock_file, "w")
                fcntl.flock(port_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.port_lock_file = port_lock_file
                return port
            except IOError:
                if port_lock_file:
                    port_lock_file.close()
                continue
        return -1

    def release_port(self):
        if self.port_lock_file:
            fcntl.flock(self.port_lock_file, fcntl.LOCK_UN)
            self.port_lock_file.close()
            self.port_lock_file = None

    def use_device(self):
        time.sleep(random.random())
        for device, cnt in self.available_devices.items():
            for i in range(cnt):
                device_lock_file = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "tmp",
                    f"gpu_{device}_{i}.lock",
                )
                try:
                    device_lock_file = open(device_lock_file, "w")
                    fcntl.flock(device_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.device_lock_file = device_lock_file
                    return device, i
                except IOError:
                    if device_lock_file:
                        device_lock_file.close()
                    continue
        return -1, -1

    def release_device(self):
        if self.device_lock_file:
            fcntl.flock(self.device_lock_file, fcntl.LOCK_UN)
            self.device_lock_file.close()
            self.device_lock_file = None

    def __init__(self, task):
        self.device_lock_file = None
        self.port_lock_file = None
        self.port = self.use_port()
        if self.port == -1:
            raise Exception("All ports are not available.")
        self.device, self.device_task_index = self.use_device()
        if self.device == -1:
            raise Exception("All devices are at full capacity now.")

        # 检查必要的文件
        icd_path = None
        layers_path = None
        if os.path.exists(ICD_PATH_1):
            icd_path = ICD_PATH_1
        elif os.path.exists(ICD_PATH_2):
            icd_path = ICD_PATH_2
        else:
            raise Exception(
                "Missing nvidia_icd.json file. Typical paths:\n"
                "- /usr/share/vulkan/icd.d/nvidia_icd.json or\n"
                "- /etc/vulkan/icd.d/nvidia_icd.json\n"
                "You can google nvidia_icd.json for your distro to find the correct path.\n"
                "Consider updating your driver to 525 if you cannot find the file.\n"
                "To continue update the ICD_PATH_1 at the top of the `src/server/tasks/omnigibson/container.py` file and retry.\n"
            )
        if os.path.exists(LAYERS_PATH_1):
            layers_path = LAYERS_PATH_1
        elif os.path.exists(LAYERS_PATH_2):
            layers_path = LAYERS_PATH_2
        elif os.path.exists(LAYERS_PATH_3):
            layers_path = LAYERS_PATH_3
        else:
            raise Exception(
                "Missing nvidia_layers.json file. Typical paths:\n"
                "- /usr/share/vulkan/icd.d/nvidia_layers.json\n"
                "- /usr/share/vulkan/implicit_layer.d/nvidia_layers.json\n"
                "- /etc/vulkan/implicit_layer.d/nvidia_layers.json\n"
                "You can google nvidia_layers.json for your distro to find the correct path.\n"
                "Consider updating your driver to 525 if you cannot find the file.\n"
                "To continue update the LAYERS_PATH_1 at the top of the `src/server/tasks/omnigibson/container.py` file and retry.\n"
            )
        if not os.path.exists(EGL_VENDOR_PATH):
            raise Exception(
                f"Missing EGL_VENDOR_PATH file.\n"
                "(default path: /usr/share/glvnd/egl_vendor.d/10_nvidia.json)\n"
                "To continue update the EGL_VENDOR_PATH at the top of the `src/server/tasks/omnigibson/container.py` file and retry.\n"
            )

        # 根据use_apptainer决定使用Docker还是Apptainer
        if self.use_apptainer:
            self._run_with_apptainer(task, icd_path, layers_path)
        else:
            self._run_with_docker(task, icd_path, layers_path)

        self.initial_reward = None
        self.final_reward = None

    def _run_with_docker(self, task, icd_path, layers_path):
        """使用Docker运行容器"""
        try:
            self.client = docker.from_env()
        except Exception as e:
            raise Exception(
                f"无法初始化Docker客户端: {str(e)}. 请确保Docker已安装并正在运行，或设置use_apptainer=True"
            )

        device_request = DeviceRequest(
            capabilities=[["gpu"]], device_ids=[f"{self.device}"]
        )

        volumes = {
            icd_path: {"bind": "/etc/vulkan/icd.d/nvidia_icd.json"},
            layers_path: {"bind": "/etc/vulkan/implicit_layer.d/nvidia_layers.json"},
            EGL_VENDOR_PATH: {"bind": "/usr/share/glvnd/egl_vendor.d/10_nvidia.json"},
            f"{self.data_dir}/datasets": {"bind": "/data"},
            f"{self.data_dir}/isaac-sim/cache/kit": {
                "bind": "/isaac-sim/kit/cache/Kit",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/cache/ov": {
                "bind": "/root/.cache/ov",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/cache/pip": {
                "bind": "/root/.cache/pip",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/cache/glcache": {
                "bind": "/root/.cache/nvidia/GLCache",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/cache/computecache": {
                "bind": "/root/.nv/ComputeCache",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/logs": {
                "bind": "/root/.nvidia-omniverse/logs",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/config": {
                "bind": "/root/.nvidia-omniverse/config",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/data": {
                "bind": "/root/.local/share/ov/data",
                "mode": "rw",
            },
            f"{self.data_dir}/isaac-sim/documents": {
                "bind": "/root/Documents",
                "mode": "rw",
            },
            f"{self.data_dir}/GoogleNews-vectors-negative300.bin": {
                "bind": "/GoogleNews-vectors-negative300.bin",
                "mode": "rw",
            },
            f"{self.data_dir}/activity_definitions": {
                "bind": "/micromamba/envs/omnigibson/lib/python3.7/site-packages/bddl/activity_definitions",
                "mode": "rw",
            },
            f"{self.modified_omnigibson_src}/inside.py": {
                "bind": "/omnigibson-src/omnigibson/object_states/inside.py",
                "mode": "rw",
            },
            f"{self.modified_omnigibson_src}/on_top.py": {
                "bind": "/omnigibson-src/omnigibson/object_states/on_top.py",
                "mode": "rw",
            },
            f"{self.modified_omnigibson_src}/under.py": {
                "bind": "/omnigibson-src/omnigibson/object_states/under.py",
                "mode": "rw",
            },
            f"{self.modified_omnigibson_src}/next_to.py": {
                "bind": "/omnigibson-src/omnigibson/object_states/next_to.py",
                "mode": "rw",
            },
            f"{self.modified_omnigibson_src}/vision_sensor.py": {
                "bind": "/omnigibson-src/omnigibson/sensors/vision_sensor.py",
                "mode": "rw",
            },
            f"{self.modified_omnigibson_src}/behavior_task.py": {
                "bind": "/omnigibson-src/omnigibson/tasks/behavior_task.py",
                "mode": "rw",
            },
            self.vab_source_dir: {"bind": "/VAB-OmniGibson-code", "mode": "rw"},
            self.output_dir: {"bind": "/og_logs", "mode": "rw"},
        }
        environment = {"OMNIGIBSON_HEADLESS": "1"}
        self.container = self.client.containers.run(
            self.docker_image,
            f"python main.py --task {task[0]} --scene {task[1]} --port {self.port} --max_round {self.max_round}",
            environment=environment,
            volumes=volumes,
            detach=True,
            stdin_open=True,
            tty=True,
            auto_remove=True,
            device_requests=[device_request],
            ports={f"{self.port}/tcp": self.port},
        )
        print(f"Started Docker container with ID: {self.container.id}")

    def _run_with_apptainer(self, task, icd_path, layers_path):
        """使用Apptainer运行容器"""
        # 构建绑定参数
        bind_mounts = [
            f"{icd_path}:/etc/vulkan/icd.d/nvidia_icd.json",
            f"{layers_path}:/etc/vulkan/implicit_layer.d/nvidia_layers.json",
            f"{EGL_VENDOR_PATH}:/usr/share/glvnd/egl_vendor.d/10_nvidia.json",
            f"{self.data_dir}/datasets:/data",
            f"{self.data_dir}/isaac-sim/cache/kit:/isaac-sim/kit/cache/Kit",
            f"{self.data_dir}/isaac-sim/cache/ov:/root/.cache/ov",
            f"{self.data_dir}/isaac-sim/cache/pip:/root/.cache/pip",
            f"{self.data_dir}/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache",
            f"{self.data_dir}/isaac-sim/cache/computecache:/root/.nv/ComputeCache",
            f"{self.data_dir}/isaac-sim/logs:/root/.nvidia-omniverse/logs",
            f"{self.data_dir}/isaac-sim/config:/root/.nvidia-omniverse/config",
            f"{self.data_dir}/isaac-sim/data:/root/.local/share/ov/data",
            f"{self.data_dir}/isaac-sim/documents:/root/Documents",
            f"{self.data_dir}/GoogleNews-vectors-negative300.bin:/GoogleNews-vectors-negative300.bin",
            f"{self.data_dir}/activity_definitions:/micromamba/envs/omnigibson/lib/python3.7/site-packages/bddl/activity_definitions",
            f"{self.modified_omnigibson_src}/inside.py:/omnigibson-src/omnigibson/object_states/inside.py",
            f"{self.modified_omnigibson_src}/on_top.py:/omnigibson-src/omnigibson/object_states/on_top.py",
            f"{self.modified_omnigibson_src}/under.py:/omnigibson-src/omnigibson/object_states/under.py",
            f"{self.modified_omnigibson_src}/next_to.py:/omnigibson-src/omnigibson/object_states/next_to.py",
            f"{self.modified_omnigibson_src}/vision_sensor.py:/omnigibson-src/omnigibson/sensors/vision_sensor.py",
            f"{self.modified_omnigibson_src}/behavior_task.py:/omnigibson-src/omnigibson/tasks/behavior_task.py",
            f"{self.vab_source_dir}:/VAB-OmniGibson-code",
            f"{self.output_dir}:/og_logs",
        ]

        # 设置环境变量
        os.environ["OMNIGIBSON_HEADLESS"] = "1"
        # 设置可见GPU设备
        os.environ["APPTAINERENV_CUDA_VISIBLE_DEVICES"] = str(self.device)
        # 设置网络相关环境变量，确保服务绑定到0.0.0.0
        os.environ["APPTAINERENV_SERVER_HOST"] = "0.0.0.0"  # 绑定到所有网络接口
        os.environ["APPTAINERENV_CLIENT_HOST"] = "localhost"  # 客户端连接地址

        # 确保SIF镜像文件存在
        sif_image_path = self.ensure_sif_image(self.docker_image)

        # 获取主机IP
        hostname = socket.gethostname()
        try:
            host_ip = socket.gethostbyname(hostname)
            print(f"主机名: {hostname}, IP地址: {host_ip}")
        except:
            host_ip = "127.0.0.1"
            print(f"无法获取主机IP地址，使用默认值: {host_ip}")

        # 使用Apptainer运行容器
        # 使用环境变量而非命令行参数，以更好地兼容现有代码
        command = f"python main.py --task {task[0]} --scene {task[1]} --port {self.port} --max_round {self.max_round}"

        # 构建Apptainer运行命令
        apptainer_cmd = [
            "apptainer",
            "run",
            "--nv",  # 使用--nv标志支持NVIDIA GPU
            "--pwd",
            "/VAB-OmniGibson-code",  # 设置工作目录
        ]

        # 不使用--network和--dns参数，它们可能导致命令行解析问题
        # Apptainer默认使用主机网络，所以不需要显式指定

        # 添加所有绑定挂载
        for bind in bind_mounts:
            apptainer_cmd.extend(["--bind", bind])

        # 设置环境变量，确保容器内的服务绑定到所有可用网络接口
        apptainer_cmd.extend(["--env", f"OMNIGIBSON_HOST=0.0.0.0"])
        apptainer_cmd.extend(["--env", f"HOST=0.0.0.0"])
        apptainer_cmd.extend(["--env", f"LISTEN_ADDR=0.0.0.0"])
        apptainer_cmd.extend(["--env", f"CLIENT_HOST={host_ip}"])
        apptainer_cmd.extend(["--env", f"SERVER_PORT={self.port}"])

        # 打印完整命令便于调试
        command_str = " ".join(apptainer_cmd + [sif_image_path, command])
        print(f"运行Apptainer命令: {command_str}")
        print(f"SIF镜像路径: {sif_image_path}")
        print(f"容器内运行命令: {command}")

        # 检查SIF文件是否真实存在
        if not os.path.exists(sif_image_path):
            raise Exception(
                f"错误: SIF镜像文件 {sif_image_path} 不存在。请确保正确构建了SIF文件。"
            )

        # 运行Apptainer命令
        try:
            self.apptainer_process = subprocess.Popen(
                apptainer_cmd + [sif_image_path, command],  # 直接使用列表拼接更可靠
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # 行缓冲，这样可以实时获取输出
                universal_newlines=True,  # 确保文本模式
            )
            print(f"Started Apptainer container with PID: {self.apptainer_process.pid}")

            # 等待一段时间，检查进程是否立即失败
            time.sleep(2)
            if self.apptainer_process.poll() is not None:
                stderr = (
                    self.apptainer_process.stderr.read()
                    if self.apptainer_process.stderr
                    else ""
                )
                stdout = (
                    self.apptainer_process.stdout.read()
                    if self.apptainer_process.stdout
                    else ""
                )
                raise Exception(
                    f"Apptainer进程启动后立即退出，退出码: {self.apptainer_process.poll()}\n标准错误: {stderr}\n标准输出: {stdout}"
                )

        except Exception as e:
            self.release_device()
            self.release_port()
            raise Exception(f"Failed to start Apptainer container: {e}")

    async def execute(self, session):
        # 根据不同的容器类型执行不同的检查
        if self.use_apptainer:
            return await self._execute_apptainer(session)
        else:
            return await self._execute_docker(session)

    async def _execute_docker(self, session):
        """使用Docker执行任务"""
        while True:
            self.container.reload()
            if self.container.status == "exited":
                return TaskSampleExecutionResult(status=SampleStatus.TASK_ERROR)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("localhost", self.port))
                data = s.recv(8192)
                if not data:
                    s.close()
                    continue
                else:
                    break
            except:
                time.sleep(4)

        return await self._process_data(session, s, data)

    async def _execute_apptainer(self, session):
        """使用Apptainer执行任务"""
        # 检查进程是否仍在运行
        if self.apptainer_process.poll() is not None:
            print(f"Apptainer进程已退出，退出码: {self.apptainer_process.poll()}")
            # 尝试获取进程的错误输出
            stderr = (
                self.apptainer_process.stderr.read()
                if self.apptainer_process.stderr
                else "无法获取错误输出"
            )
            print(f"Apptainer进程错误输出: {stderr}")
            return TaskSampleExecutionResult(status=SampleStatus.TASK_ERROR)

        # 等待一段时间，确保容器内的服务启动完成
        time.sleep(5)

        # 打印进程状态进行调试
        print(f"Apptainer进程状态: 正在运行, PID: {self.apptainer_process.pid}")

        # 尝试连接到服务器端口
        connect_attempts = 0
        max_attempts = 30  # 增加最大尝试次数
        while connect_attempts < max_attempts:
            connect_attempts += 1

            if self.apptainer_process.poll() is not None:
                print(
                    f"尝试连接过程中Apptainer进程退出，退出码: {self.apptainer_process.poll()}"
                )
                stderr = (
                    self.apptainer_process.stderr.read()
                    if self.apptainer_process.stderr
                    else "无法获取错误输出"
                )
                print(f"Apptainer进程错误输出: {stderr}")
                return TaskSampleExecutionResult(status=SampleStatus.TASK_ERROR)

            try:
                # 先尝试localhost
                print(f"尝试连接 localhost:{self.port}，第{connect_attempts}次尝试...")
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)  # 设置连接超时
                s.connect(("localhost", self.port))
                data = s.recv(8192)
                if not data:
                    print(f"连接成功但未收到数据，关闭连接并重试...")
                    s.close()
                    time.sleep(2)
                    continue
                else:
                    print(f"成功连接到localhost:{self.port}并接收到数据")
                    break
            except socket.timeout:
                print(f"连接localhost:{self.port}超时")
                s.close()
            except Exception as e:
                print(f"连接localhost:{self.port}失败: {str(e)}")
                s.close()

                # 如果localhost连接失败，尝试使用127.0.0.1
                try:
                    print(f"尝试连接 127.0.0.1:{self.port}...")
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(5)
                    s.connect(("127.0.0.1", self.port))
                    data = s.recv(8192)
                    if not data:
                        print(f"连接成功但未收到数据，关闭连接并重试...")
                        s.close()
                        time.sleep(2)
                        continue
                    else:
                        print(f"成功连接到127.0.0.1:{self.port}并接收到数据")
                        break
                except Exception as e2:
                    print(f"连接127.0.0.1:{self.port}也失败: {str(e2)}")
                    s.close()
                    time.sleep(4)  # 等待一段时间后重试

            # 如果进程有标准输出，打印出来帮助调试
            if (
                hasattr(self.apptainer_process, "stdout")
                and self.apptainer_process.stdout
            ):
                try:
                    stdout_data = self.apptainer_process.stdout.readline()
                    if stdout_data:
                        print(f"Apptainer进程输出: {stdout_data.decode().strip()}")
                except:
                    pass

        if connect_attempts >= max_attempts:
            print(f"达到最大尝试次数({max_attempts})，无法连接到容器服务")
            # 终止进程
            if self.apptainer_process.poll() is None:
                print(f"终止Apptainer进程...")
                self.apptainer_process.terminate()
            return TaskSampleExecutionResult(status=SampleStatus.TASK_ERROR)

        return await self._process_data(session, s, data)

    async def _process_data(self, session, s, data):
        """处理从容器接收到的数据"""
        data = data.decode()
        reward = float(data.split("<RREWARD>")[-1].split("</RREWARD>")[0])
        if self.initial_reward == None:
            self.initial_reward = reward
        if "<DDONE>" in data and "</DDONE>" in data:
            done_message = data.split("<DDONE>")[-1].split("</DDONE>")[0]
            self.final_reward = reward
            if "task limit reached" in done_message:
                s.sendall("okay".encode())
                return TaskSampleExecutionResult(
                    status=SampleStatus.TASK_LIMIT_REACHED,
                    result={
                        "success": False,
                        "initial_reward": self.initial_reward,
                        "final_reward": self.final_reward,
                    },
                )
            elif "agent invalid action" in done_message:
                s.sendall("okay".encode())
                return TaskSampleExecutionResult(
                    status=SampleStatus.AGENT_INVALID_ACTION,
                    result={
                        "success": False,
                        "initial_reward": self.initial_reward,
                        "final_reward": self.final_reward,
                    },
                )
            elif "task error" in done_message:
                s.sendall("okay".encode())
                return TaskSampleExecutionResult(
                    status=SampleStatus.TASK_ERROR,
                    result={
                        "success": False,
                        "initial_reward": self.initial_reward,
                        "final_reward": self.final_reward,
                    },
                )
            elif "task failed" in done_message:
                s.sendall("okay".encode())
                return TaskSampleExecutionResult(
                    status=SampleStatus.COMPLETED,
                    result={
                        "success": False,
                        "initial_reward": self.initial_reward,
                        "final_reward": self.final_reward,
                    },
                )
            elif "task completed successfully" in done_message:
                s.sendall("okay".encode())
                return TaskSampleExecutionResult(
                    status=SampleStatus.COMPLETED,
                    result={
                        "success": True,
                        "initial_reward": self.initial_reward,
                        "final_reward": self.final_reward,
                    },
                )
        image_path = data.split("<IIMAGE>")[-1].split("</IIMAGE>")[0]
        text_prompt = data.split(f"<IIMAGE>{image_path}</IIMAGE>")[0]
        image_path = image_path.replace("/og_logs", self.output_dir)
        session.inject(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_path}",
                            "detail": "high",
                        },
                    },
                ],
            }
        )
        # print(text_prompt)
        message = await session.action()
        print(message)
        if message.status == AgentOutputStatus.AGENT_CONTEXT_LIMIT:
            return TaskSampleExecutionResult(status=SampleStatus.AGENT_CONTEXT_LIMIT)
        elif message.status != AgentOutputStatus.NORMAL:
            return TaskSampleExecutionResult(status=SampleStatus.UNKNOWN)
        message = message.content
        if isinstance(message, tuple):
            message = message[0]
        if "Action Feedback:" in message:
            message = message.split("Action Feedback:")[0]
        if message.count("ACTION") >= 2:
            message_parts = message.split("ACTION", 2)
            message = "ACTION".join(message_parts[:2])
        if message.count("OBSERVATION") >= 2:
            message_parts = message.split("OBSERVATION", 2)
            message = "OBSERVATION".join(message_parts[:2])
        if "\n<|end_of_text|>" in message:
            message = message.split("\n<|end_of_text|>")[0]
        if "<|end_of_text|>" in message:
            message = message.split("<|end_of_text|>")[0]
        s.sendall(message.encode())
        session.history[-1].content = message
        session.history[-2].content = [
            {"type": "text", "text": text_prompt + "Omitted.\n"}
        ]
        return TaskSampleExecutionResult(status=SampleStatus.RUNNING)

    def close(self):
        try:
            if self.use_apptainer:
                if (
                    hasattr(self, "apptainer_process")
                    and self.apptainer_process
                    and self.apptainer_process.poll() is None
                ):
                    # 先尝试正常终止
                    self.apptainer_process.terminate()
                    # 给进程一些时间来清理
                    time.sleep(4)
                    # 如果进程仍在运行，强制终止
                    if self.apptainer_process.poll() is None:
                        self.apptainer_process.kill()
            else:
                if hasattr(self, "container"):
                    time.sleep(4)
                    if not self.container.status == "exit":
                        self.container.stop(timeout=24)
        except:
            pass

        self.release_device()
        self.release_port()
