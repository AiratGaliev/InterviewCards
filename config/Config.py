# config/Config.py

import configparser
import os
import platform
import re
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


class Config(Enum):
    @staticmethod
    def get_config():
        config = configparser.ConfigParser()
        config.read('config.ini', encoding='utf-8')
        return config

    @staticmethod
    def get_path_by_platform():
        if platform.system() == 'Windows':
            return 'directories-windows'
        elif platform.system() == 'Linux':
            return 'directories-linux'
        return 'directories-linux'

    _config = get_config()

    CATEGORIES: str = _config['main']['categories']
    CATEGORIES_LIST: list[str] = re.split(r'\s*,\s*', str(CATEGORIES))
    CATEGORY_ID: str = _config['main']['category_id']
    DOCUMENTS: str = os.path.expanduser('~') + _config['main']['documents']
    OUTPUT: str = os.path.expanduser('~') + _config['main']['output']
    OBSIDIAN_VAULT: str = os.path.expanduser('~') + _config[get_path_by_platform()]['obsidian_vault']
    ANKI_COLLECTION_MEDIA: str = os.path.expanduser('~') + _config[get_path_by_platform()]['anki_collection_media']

    MATERIALS_SOURCE: str = os.path.join(OBSIDIAN_VAULT, "Interview", "Materials")
    MAX_QUESTIONS_PER_DECK: int = int(_config['limits']['max_questions_per_deck'])
    MAX_QUESTION_LENGTH: int = int(_config['limits']['max_question_length'])
    MAX_ANSWER_LENGTH: int = int(_config['limits']['max_answer_length'])

    CARD_TAG: str = _config['spaced_repetition']['card_tag']
    TOPIC_TAG: str = _config['spaced_repetition']['topic_tag']
    DIFFICULTY_TAG_PREFIX: str = _config['spaced_repetition']['difficulty_tag_prefix']

    @staticmethod
    def create_directories():
        """Создание всех необходимых директорий"""
        dirs = [
            Config.DOCUMENTS.value,
            Config.OUTPUT.value,
            os.path.join(Config.OUTPUT.value, 'anki'),
            Config.OBSIDIAN_VAULT.value,
            os.path.join(Config.OBSIDIAN_VAULT.value, 'Interview', 'Materials'),
            os.path.join(Config.OBSIDIAN_VAULT.value, 'Interview', 'Cards')
        ]
        for dir_path in dirs:
            os.makedirs(dir_path, exist_ok=True)
            print(f"✅ Директория создана: {dir_path}")
