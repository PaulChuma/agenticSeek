
import os
import subprocess
import threading
from sources.logger import Logger
from sources.utility import pretty_print
from sources.llm_provider import Provider


class ProjectBuilderAgent:
    """
    Агент для автоматизации создания структуры и файлов проекта по текстовому описанию,
    генерации кода с помощью LLM, запуска Docker/CLI-команд и отслеживания статуса.
    """
    def __init__(self, base_path="generated_projects"):
        self.base_path = base_path
        self.logger = Logger("project_builder.log")
        self.status = "idle"
        self.last_output = ""
        self.provider = Provider(
            provider_name=os.environ.get("PROVIDER_NAME", "gpt-oss"),
            model=os.environ.get("PROVIDER_MODEL", "20"),
            server_address=os.environ.get("PROVIDER_SERVER_ADDRESS", "http://localhost:11434"),
            is_local=True
        )
        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path)

    def build_project(self, project_name: str, structure: dict, files: dict):
        """
        Создаёт структуру директорий и файлов.
        :param project_name: Имя проекта
        :param structure: dict с папками и подпапками
        :param files: dict с файлами и их содержимым
        """
        project_root = os.path.join(self.base_path, project_name)
        os.makedirs(project_root, exist_ok=True)
        # Создание директорий
        for folder in structure.get("folders", []):
            os.makedirs(os.path.join(project_root, folder), exist_ok=True)
        # Создание файлов
        for rel_path, content in files.items():
            file_path = os.path.join(project_root, rel_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        pretty_print(f"Проект {project_name} успешно создан в {project_root}", color="success")
        self.logger.info(f"Project {project_name} created at {project_root}")
        self.status = f"Project {project_name} created"
        return project_root

    def generate_from_description(self, description: str):
        """
        Генерирует структуру и файлы проекта с помощью LLM по описанию.
        """
        self.status = "generating project..."
        prompt = (
            "Ты — AI-инженер. На основе следующего описания сгенерируй структуру проекта (JSON: folders, files) "
            "и содержимое ключевых файлов (README.md, main.py, requirements.txt и др.) для Python-проекта.\n"
            f"Описание: {description}\n"
            "Ответ: JSON с ключами 'folders' (list) и 'files' (dict: путь->содержимое)."
        )
        try:
            response = self.provider.respond([{"role": "user", "content": prompt}], verbose=False)
            import json
            data = json.loads(response) if isinstance(response, str) else response
            project_name = data.get("project_name", "ai_engineer_assistant")
            structure = {"folders": data.get("folders", [])}
            files = data.get("files", {"README.md": description})
            return self.build_project(project_name, structure, files)
        except Exception as e:
            self.logger.error(f"LLM generation failed: {e}")
            self.status = f"error: {e}"
            return None

    def run_docker_or_cli(self, project_name: str, command: str):
        """
        Запускает Docker или shell-команду в каталоге проекта, сохраняет вывод и статус.
        """
        project_root = os.path.join(self.base_path, project_name)
        self.status = f"running: {command}"
        def run():
            try:
                result = subprocess.run(command, shell=True, cwd=project_root, capture_output=True, text=True, timeout=300)
                self.last_output = result.stdout + '\n' + result.stderr
                self.status = f"done: {command}"
            except Exception as e:
                self.last_output = str(e)
                self.status = f"error: {e}"
        thread = threading.Thread(target=run)
        thread.start()
        return "started"

    def get_status(self):
        """
        Возвращает текущий статус и последний вывод.
        """
        return {"status": self.status, "output": self.last_output}
