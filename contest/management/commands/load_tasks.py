import json
from django.core.management.base import BaseCommand
from django.db import transaction
from contest.models import Task  # Замените yourapp на название вашего приложения


class Command(BaseCommand):
    help = 'Массовая загрузка задач и тестов из JSON файла'

    def add_arguments(self, parser):
        # Добавляем аргумент для указания пути к файлу
        parser.add_argument('json_file', type=str, help='Путь к JSON файлу с задачами')

    def handle(self, *args, **kwargs):
        json_file = kwargs['json_file']

        try:
            with open(json_file, 'r', encoding='utf-8') as file:
                tasks_data = json.load(file)
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f'Файл {json_file} не найден.'))
            return
        except json.JSONDecodeError:
            self.stderr.write(self.style.ERROR('Ошибка форматирования JSON файла.'))
            return

        tasks_created = 0
        tests_counted = 0

        # Оборачиваем в транзакцию для безопасности базы данных
        with transaction.atomic():
            for item in tasks_data:
                # Извлекаем тесты из JSON (если их нет, берем пустой список)
                test_cases = item.get('test_cases', [])

                # Создаем задачу и сразу передаем JSON-объект в поле test_cases
                Task.objects.create(
                    title=item.get('title', 'Без названия'),
                    level=item.get('level', 'A'),
                    description=item.get('description', ''),
                    input_example=item.get('input_example', ''),
                    output_example=item.get('output_example', ''),
                    test_cases=test_cases
                )
                tasks_created += 1
                tests_counted += len(test_cases)

        self.stdout.write(self.style.SUCCESS(
            f'Успешно загружено {tasks_created} задач (включая {tests_counted} тест-кейсов внутри JSON-полей)!'
        ))