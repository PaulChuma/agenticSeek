#!/usr/bin/env python3

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os
import uvicorn
import aiofiles
import configparser
import asyncio
import time
from typing import List

from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uuid
from sources.llm_provider import Provider
from sources.interaction import Interaction
from sources.agents import CasualAgent, CoderAgent, FileAgent, PlannerAgent, BrowserAgent
from sources.browser import Browser, create_driver
from sources.utility import pretty_print
from sources.logger import Logger
from sources.schemas import QueryRequest, QueryResponse
from dotenv import load_dotenv
load_dotenv()

# ... остальной код без изменений ...

def is_running_in_docker():
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup', 'r') as f:
            return 'docker' in f.read()
    except:
        pass
    return False

from celery import Celery

# --- FastAPI и CORS ---
api = FastAPI(title="AgenticSeek API", version="0.1.0")
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # или ["http://localhost:3000"] для фронта
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Celery ---
celery_app = Celery("tasks", broker="redis://localhost:6379/0", backend="redis://localhost:6379/0")
celery_app.conf.update(task_track_started=True)

# --- Project Builder Agent ---

from sources.agents.project_builder_agent import ProjectBuilderAgent
from sources.agents.planner_agent2 import PlannerAgent2
project_builder_agent = ProjectBuilderAgent()
planner_agent2 = PlannerAgent2()
# --- Project Builder Agent ---
@api.post("/plan_project")
async def plan_project(request: QueryRequest):
    """
    Создать план по описанию задачи (PlannerAgent2).
    """
    plan = planner_agent2.plan(request.query)
    return {"plan": plan.to_dict() if plan else None}

@api.get("/task_status")
async def task_status():
    """
    Получить статус и memory по задачам PlannerAgent2.
    """
    return {"plan": planner_agent2.get_plan(), "memory": planner_agent2.get_memory()}

@api.post("/feedback")
async def feedback_endpoint(feedback: str):
    """
    Отправить feedback для корректировки плана.
    """
    planner_agent2.replan(feedback)
    return {"status": "replanned", "plan": planner_agent2.get_plan()}


@api.post("/build_project")
async def build_project_endpoint(request: QueryRequest):
    """
    Принимает текстовое описание проекта, генерирует структуру и файлы с помощью LLM.
    """
    description = request.query
    project_path = project_builder_agent.generate_from_description(description)
    return {"status": project_builder_agent.status, "project_path": project_path}

@api.post("/run_project_command")
async def run_project_command(project_name: str, command: str):
    """
    Запускает Docker или shell-команду в каталоге проекта.
    """
    result = project_builder_agent.run_docker_or_cli(project_name, command)
    return {"status": result}

@api.get("/project_status")
async def project_status():
    """
    Возвращает статус и вывод последней команды ProjectBuilderAgent.
    """
    return project_builder_agent.get_status()

# --- Логгер и конфиг ---
logger = Logger("backend.log")
config = configparser.ConfigParser()
config.read('config.ini')

# --- Статика для скриншотов ---
if not os.path.exists(".screenshots"):
    os.makedirs(".screenshots")
api.mount("/screenshots", StaticFiles(directory=".screenshots"), name="screenshots")

# --- Инициализация системы ---
def initialize_system():
    stealth_mode = config.getboolean('BROWSER', 'stealth_mode')
    personality_folder = "jarvis" if config.getboolean('MAIN', 'jarvis_personality') else "base"
    languages = config["MAIN"]["languages"].split(' ')
    
    headless = config.getboolean('BROWSER', 'headless_browser')
    if is_running_in_docker() and not headless:
        print("\n" + "*" * 70)
        print("*** WARNING: Detected Docker environment - forcing headless_browser=True ***")
        print("*** INFO: To see the browser, run 'python cli.py' on your host machine ***")
        print("*" * 70 + "\n")
        sys.stdout.flush()
        logger.warning("Detected Docker environment - forcing headless_browser=True")
        logger.info("To see the browser, run 'python cli.py' on your host machine instead")
        headless = True
    
    provider = Provider(
        provider_name=config["MAIN"]["provider_name"],
        model=config["MAIN"]["provider_model"],
        server_address=config["MAIN"]["provider_server_address"],
        is_local=config.getboolean('MAIN', 'is_local')
    )
    logger.info(f"Provider initialized: {provider.provider_name} ({provider.model})")

    browser = Browser(
        create_driver(headless=headless, stealth_mode=stealth_mode, lang=languages[0]),
        anticaptcha_manual_install=stealth_mode
    )
    logger.info("Browser initialized")

    agents = [
        CasualAgent(
            name=config["MAIN"]["agent_name"],
            prompt_path=f"prompts/{personality_folder}/casual_agent.txt",
            provider=provider, verbose=False
        ),
        CoderAgent(
            name="coder",
            prompt_path=f"prompts/{personality_folder}/coder_agent.txt",
            provider=provider, verbose=False
        ),
        FileAgent(
            name="File Agent",
            prompt_path=f"prompts/{personality_folder}/file_agent.txt",
            provider=provider, verbose=False
        ),
        BrowserAgent(
            name="Browser",
            prompt_path=f"prompts/{personality_folder}/browser_agent.txt",
            provider=provider, verbose=False, browser=browser
        ),
        PlannerAgent(
            name="Planner",
            prompt_path=f"prompts/{personality_folder}/planner_agent.txt",
            provider=provider, verbose=False, browser=browser
        )
    ]
    logger.info("Agents initialized")

    interaction = Interaction(
        agents,
        tts_enabled=config.getboolean('MAIN', 'speak'),
        stt_enabled=config.getboolean('MAIN', 'listen'),
        recover_last_session=config.getboolean('MAIN', 'recover_last_session'),
        langs=languages
    )
    logger.info("Interaction initialized")
    return interaction

interaction = initialize_system()
generation_lock = asyncio.Lock()
query_resp_history = []

# --- Эндпоинты ---
@api.get("/screenshot")
async def get_screenshot():
    logger.info("Screenshot endpoint called")
    screenshot_path = ".screenshots/updated_screen.png"
    if os.path.exists(screenshot_path):
        return FileResponse(screenshot_path)
    logger.error("No screenshot available")
    return JSONResponse(status_code=404, content={"error": "No screenshot available"})

@api.get("/health")
async def health_check():
    logger.info("Health check endpoint called")
    return {"status": "healthy", "version": "0.1.0"}

@api.get("/is_active")
async def is_active():
    logger.info("Is active endpoint called")
    return {"is_active": interaction.is_active}

@api.get("/stop")
async def stop():
    logger.info("Stop endpoint called")
    interaction.current_agent.request_stop()
    return JSONResponse(status_code=200, content={"status": "stopped"})

@api.get("/latest_answer")
async def get_latest_answer():
    if interaction.current_agent is None:
        return JSONResponse(status_code=404, content={"error": "No agent available"})
    uid = str(uuid.uuid4())
    last_answer = interaction.current_agent.last_answer
    if not any(q["answer"] == last_answer for q in query_resp_history):
        query_resp = {
            "done": "false",
            "answer": last_answer,
            "reasoning": interaction.current_agent.last_reasoning,
            "agent_name": interaction.current_agent.agent_name if interaction.current_agent else "None",
            "success": interaction.current_agent.success,
            "blocks": {f'{i}': block.jsonify() for i, block in enumerate(interaction.get_last_blocks_result())} if interaction.current_agent else {},
            "status": interaction.current_agent.get_status_message if interaction.current_agent else "No status available",
            "uid": uid
        }
        interaction.current_agent.last_answer = ""
        interaction.current_agent.last_reasoning = ""
        query_resp_history.append(query_resp)
        return JSONResponse(status_code=200, content=query_resp)
    if query_resp_history:
        return JSONResponse(status_code=200, content=query_resp_history[-1])
    return JSONResponse(status_code=404, content={"error": "No answer available"})

async def think_wrapper(interaction, query):
    try:
        interaction.last_query = query
        logger.info("Agents request is being processed")
        success = await interaction.think()
        if not success:
            interaction.last_answer = "Error: No answer from agent"
            interaction.last_reasoning = "Error: No reasoning from agent"
            interaction.last_success = False
        else:
            interaction.last_success = True
        pretty_print(interaction.last_answer)
        interaction.speak_answer()
        return success
    except Exception as e:
        logger.error(f"Error in think_wrapper: {str(e)}")
        interaction.last_answer = ""
        interaction.last_reasoning = f"Error: {str(e)}"
        interaction.last_success = False
        raise e

@api.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest):
    logger.info(f"Processing query: {request.query}")

    query_resp = QueryResponse(
        done="false",
        answer="",
        reasoning="",
        agent_name="Unknown",
        success="false",
        blocks={},
        status="Ready",
        uid=str(uuid.uuid4())
    )

    async with generation_lock:
        success = await think_wrapper(interaction, request.query)

        if not success:
            query_resp.answer = interaction.last_answer
            query_resp.reasoning = interaction.last_reasoning
            return JSONResponse(status_code=400, content=query_resp.jsonify())

        if interaction.current_agent:
            blocks_json = {f'{i}': block.jsonify() for i, block in enumerate(interaction.current_agent.get_blocks_result())}
        else:
            logger.error("No current agent found")
            query_resp.answer = "Error: No current agent"
            return JSONResponse(status_code=400, content=query_resp.jsonify())

        query_resp.done = "true"
        query_resp.answer = interaction.last_answer
        query_resp.reasoning = interaction.last_reasoning
        query_resp.agent_name = interaction.current_agent.agent_name
        query_resp.success = str(interaction.last_success)
        query_resp.blocks = blocks_json
        query_resp_history.append(query_resp.jsonify())

        logger.info("Query processed successfully")
        if config.getboolean('MAIN', 'save_session'):
            interaction.save_session()
        return JSONResponse(status_code=200, content=query_resp.jsonify())

# --- Запуск сервера ---
if __name__ == "__main__":
    if is_running_in_docker():
        print("[AgenticSeek] Starting in Docker container...")
    else:
        print("[AgenticSeek] Starting on host machine...")

    envport = os.getenv("BACKEND_PORT")
    port = int(envport) if envport else 7777
    uvicorn.run(api, host="0.0.0.0", port=port)
