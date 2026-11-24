# -*- coding: utf-8 -*-
"""Тесты пересдачи СОР/СОЧ: назначение, другие случайные задачи, две оценки, доступ."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Course, Duel, DuelQueueEntry, Exam, ExamAnswer, ExamAttempt, ExamRetake, Student, StudyRoom, Submission, LoginEvent, Tag, Task, Teacher)


def make_task(title, level, tag, ok=True):
    task = Task.objects.create(title=title, level=level, description='d', input_example='', output_example='1',
                               test_cases=[{'input': '', 'output': '1'}] if ok else [])
    task.tags.add(tag)
    return task


from .templatetags.ui_i18n import UI_TRANSLATIONS


class RetakeTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.tag1 = Tag.objects.create(name='циклы')
        self.tag2 = Tag.objects.create(name='строки')
        # по 4 задачи на условие, чтобы хватало «других»
        self.a_tasks = [make_task(f'A{i}', 'A', self.tag1) for i in range(4)]
        self.b_tasks = [make_task(f'B{i}', 'B', self.tag2) for i in range(4)]

        self.teacher_user = User.objects.create_user('teacher', password='x')
        self.teacher = Teacher.objects.create(user=self.teacher_user, full_name='Учитель', classes='7F')
        self.students = []
        for i in range(3):
            u = User.objects.create_user(f's{i}', password='x')
            self.students.append(Student.objects.create(user=u, full_name=f'Ученик {i}', school_class='7F'))
        other = User.objects.create_user('other', password='x')
        self.other_class = Student.objects.create(user=other, full_name='Чужой', school_class='8A')

        now = timezone.now()
        self.exam = Exam.objects.create(
            kind='SOR', title='СОР 1', school_class='7F', start_at=now - timedelta(hours=3),
            duration_minutes=40, created_by=self.teacher)
        self.exam.tasks.set([self.a_tasks[0], self.b_tasks[0]])   # «определённые» задачи
        self.random_exam = Exam.objects.create(
            kind='SOCH', title='СОЧ случайный', school_class='7F', start_at=now - timedelta(hours=3),
            duration_minutes=40, created_by=self.teacher, random_count=2,
            random_slots=[{'tag': self.tag1.id, 'level': 'A'}, {'tag': self.tag2.id, 'level': 'B'}])

    def attempt(self, exam, student, score_each, task_ids=None, retake=None):
        att = ExamAttempt.objects.create(exam=exam, student=student, retake=retake,
                                         started_at=timezone.now() - timedelta(hours=3),
                                         finished_at=timezone.now() - timedelta(hours=2),
                                         task_ids=task_ids or [])
        for task in att.task_list():
            ExamAnswer.objects.create(attempt=att, task=task, best_score=score_each, submissions_count=1)
        return att

    def retake(self, exam, students, start_delta=-5, minutes=60):
        rt = ExamRetake.objects.create(exam=exam, start_at=timezone.now() + timedelta(minutes=start_delta),
                                       duration_minutes=minutes, created_by=self.teacher)
        rt.students.set(students)
        return rt


class RetakeCreateTests(RetakeTestBase):
    def test_teacher_assigns_retake_only_to_chosen_students(self):
        self.client.force_login(self.teacher_user)
        start = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('retake_create', args=[self.exam.id]), {
            'students': [self.students[0].id, self.students[2].id, self.other_class.id],
            'start_at': start, 'duration_minutes': '30'})
        self.assertRedirects(resp, reverse('exam_results', args=[self.exam.id]))
        retake = ExamRetake.objects.get()
        self.assertEqual(set(retake.students.all()), {self.students[0], self.students[2]})  # чужой класс отброшен
        self.assertEqual(retake.duration_minutes, 30)
        self.assertEqual(retake.created_by, self.teacher)

    def test_form_page_and_results_have_retake_button(self):
        self.client.force_login(self.teacher_user)
        self.assertContains(self.client.get(reverse('retake_create', args=[self.exam.id])), 'Ученик 1')
        self.assertContains(self.client.get(reverse('exam_results', args=[self.exam.id])), 'Назначить пересдачу')
        self.assertContains(self.client.get(reverse('exam_list') + f'?cls={self.exam.school_class}'), 'Пересдача')

    def test_retake_only_after_exam_finished(self):
        upcoming = Exam.objects.create(kind='SOR', title='Будущий', school_class='7F',
                                       start_at=timezone.now() + timedelta(days=1), duration_minutes=40)
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse('retake_create', args=[upcoming.id]))
        self.assertRedirects(resp, reverse('exam_results', args=[upcoming.id]))

    def test_validation_errors_do_not_create(self):
        self.client.force_login(self.teacher_user)
        url = reverse('retake_create', args=[self.exam.id])
        past = (timezone.localtime() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        future = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(url, {'students': [self.students[0].id], 'start_at': past, 'duration_minutes': '30'})
        self.client.post(url, {'start_at': future, 'duration_minutes': '30'})                      # никого не выбрали
        self.client.post(url, {'students': [self.students[0].id], 'start_at': future, 'duration_minutes': '0'})
        self.assertEqual(ExamRetake.objects.count(), 0)

    def test_student_can_get_two_retakes_but_not_three(self):
        self.retake(self.exam, [self.students[0]], start_delta=60)
        self.client.force_login(self.teacher_user)
        future = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        post = lambda: self.client.post(reverse('retake_create', args=[self.exam.id]),
                                        {'students': [self.students[0].id], 'start_at': future, 'duration_minutes': '30'})
        post()
        self.assertEqual(ExamRetake.objects.count(), 2)
        post()   # третья — уже нельзя
        self.assertEqual(ExamRetake.objects.count(), 2)

    def test_second_retake_avoids_tasks_of_first_retake(self):
        from .exam_views import _retake_task_ids
        st = self.students[0]
        self.attempt(self.random_exam, st, 3, task_ids=[self.a_tasks[0].id, self.b_tasks[0].id])
        rt1 = self.retake(self.random_exam, [st], start_delta=-60)
        first = self.attempt(self.random_exam, st, 3, retake=rt1, task_ids=[self.a_tasks[1].id, self.b_tasks[1].id])
        self.retake(self.random_exam, [st], start_delta=60)
        for _ in range(10):
            ids = set(_retake_task_ids(self.random_exam, st))
            self.assertFalse(ids & {self.a_tasks[0].id, self.b_tasks[0].id, self.a_tasks[1].id, self.b_tasks[1].id})

    def test_other_teacher_cannot_manage(self):
        u = User.objects.create_user('t2', password='x')
        Teacher.objects.create(user=u, full_name='Другой', classes='9B')
        self.client.force_login(u)
        resp = self.client.get(reverse('retake_create', args=[self.exam.id]))
        self.assertRedirects(resp, reverse('exam_list'))

    def test_delete_retake_only_without_attempts(self):
        rt = self.retake(self.exam, [self.students[0]], start_delta=60)
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('retake_delete', args=[self.exam.id, rt.id]))
        self.assertFalse(ExamRetake.objects.filter(id=rt.id).exists())
        rt2 = self.retake(self.exam, [self.students[1]])
        self.attempt(self.exam, self.students[1], 3, retake=rt2, task_ids=[self.a_tasks[1].id])
        self.client.post(reverse('retake_delete', args=[self.exam.id, rt2.id]))
        self.assertTrue(ExamRetake.objects.filter(id=rt2.id).exists())


class RetakeTaskTests(RetakeTestBase):
    def start(self, student, exam, retake):
        self.client.force_login(student.user)
        self.client.post(reverse('retake_start', args=[exam.id, retake.id]))
        return ExamAttempt.objects.get(exam=exam, student=student, retake=retake)

    def test_fixed_exam_retake_gives_other_random_tasks(self):
        self.attempt(self.exam, self.students[0], 4)
        rt = self.retake(self.exam, [self.students[0]])
        for _ in range(15):   # случайность — повторим, чтобы поймать редкие совпадения
            ExamAttempt.objects.filter(retake=rt).delete()
            att = self.start(self.students[0], self.exam, rt)
            ids = att.task_ids
            self.assertEqual(len(ids), 2)
            self.assertFalse(set(ids) & {self.a_tasks[0].id, self.b_tasks[0].id})
            tasks = {t.id: t for t in att.task_list()}
            self.assertEqual(sorted(t.level for t in tasks.values()), ['A', 'B'])   # те же сложности

    def test_random_exam_retake_excludes_previous_tasks(self):
        first = self.attempt(self.random_exam, self.students[0], 2,
                             task_ids=[self.a_tasks[1].id, self.b_tasks[1].id])
        rt = self.retake(self.random_exam, [self.students[0]])
        for _ in range(15):
            ExamAttempt.objects.filter(retake=rt).delete()
            att = self.start(self.students[0], self.random_exam, rt)
            self.assertEqual(len(att.task_ids), 2)
            self.assertFalse(set(att.task_ids) & set(first.task_ids))
            levels = {t.level: t for t in att.task_list()}
            self.assertEqual(set(levels), {'A', 'B'})

    def test_relaxes_conditions_when_not_enough_other_tasks(self):
        Task.objects.filter(id__in=[t.id for t in self.a_tasks[2:]]).delete()   # осталось 2 A-задачи
        first = self.attempt(self.random_exam, self.students[0], 2,
                             task_ids=[self.a_tasks[0].id, self.b_tasks[0].id])
        rt = self.retake(self.random_exam, [self.students[0]])
        att = self.start(self.students[0], self.random_exam, rt)
        self.assertEqual(len(att.task_ids), 2)
        self.assertEqual(len(set(att.task_ids)), 2)
        self.assertFalse(set(att.task_ids) & set(first.task_ids))

    def test_student_without_exam_attempt_still_gets_other_tasks_for_fixed_exam(self):
        rt = self.retake(self.exam, [self.students[1]])
        att = self.start(self.students[1], self.exam, rt)
        self.assertFalse(set(att.task_ids) & {self.a_tasks[0].id, self.b_tasks[0].id})

    def test_two_students_may_get_different_variants(self):
        rt = self.retake(self.random_exam, [self.students[0], self.students[1]])
        variants = set()
        for _ in range(20):
            ExamAttempt.objects.filter(retake=rt).delete()
            variants.add(tuple(self.start(self.students[0], self.random_exam, rt).task_ids))
        self.assertGreater(len(variants), 1)


class RetakeAccessTests(RetakeTestBase):
    def test_unassigned_student_cannot_open_or_start(self):
        rt = self.retake(self.exam, [self.students[0]])
        self.client.force_login(self.students[1].user)
        self.assertRedirects(self.client.get(reverse('retake_detail', args=[self.exam.id, rt.id])), reverse('exam_list'))
        self.client.post(reverse('retake_start', args=[self.exam.id, rt.id]))
        self.assertFalse(ExamAttempt.objects.filter(retake=rt).exists())

    def test_cannot_start_before_retake_time(self):
        rt = self.retake(self.exam, [self.students[0]], start_delta=120)
        self.client.force_login(self.students[0].user)
        self.client.post(reverse('retake_start', args=[self.exam.id, rt.id]))
        self.assertFalse(ExamAttempt.objects.filter(retake=rt).exists())

    def test_cannot_start_after_retake_ended(self):
        rt = self.retake(self.exam, [self.students[0]], start_delta=-120, minutes=30)
        self.client.force_login(self.students[0].user)
        self.client.post(reverse('retake_start', args=[self.exam.id, rt.id]))
        self.assertFalse(ExamAttempt.objects.filter(retake=rt).exists())

    def test_courses_locked_during_active_retake_and_unlocked_after_finish(self):
        rt = self.retake(self.exam, [self.students[0]])
        self.client.force_login(self.students[0].user)
        self.client.post(reverse('retake_start', args=[self.exam.id, rt.id]))
        resp = self.client.get(reverse('courses'))
        self.assertRedirects(resp, reverse('retake_detail', args=[self.exam.id, rt.id]), fetch_redirect_response=False)
        self.client.post(reverse('retake_finish', args=[self.exam.id, rt.id]))
        self.assertEqual(self.client.get(reverse('courses')).status_code, 200)

    def test_main_exam_not_blocked_by_retake_of_other_student(self):
        self.retake(self.exam, [self.students[0]])
        self.client.force_login(self.students[1].user)
        self.assertEqual(self.client.get(reverse('courses')).status_code, 200)

    def test_submission_on_retake_task_scores_and_is_limited_to_own_tasks(self):
        rt = self.retake(self.exam, [self.students[0]])
        self.client.force_login(self.students[0].user)
        self.client.post(reverse('retake_start', args=[self.exam.id, rt.id]))
        att = ExamAttempt.objects.get(retake=rt)
        mine = att.task_ids[0]
        self.client.post(reverse('retake_task', args=[self.exam.id, rt.id, mine]), {'code': 'print(1)'})
        self.assertEqual(ExamAnswer.objects.get(attempt=att, task_id=mine).best_score, 10)
        self.assertEqual(att.grade(), 5.0)    # 10 за одну из двух задач
        foreign = self.a_tasks[0].id          # задача с самого экзамена
        resp = self.client.post(reverse('retake_task', args=[self.exam.id, rt.id, foreign]), {'code': 'print(1)'})
        self.assertRedirects(resp, reverse('retake_detail', args=[self.exam.id, rt.id]))
        self.assertFalse(ExamAnswer.objects.filter(attempt=att, task_id=foreign).exists())


class TwoGradesTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        # s0 пересдаёт, s1 — нет, обе попытки экзамена уже завершены
        self.exam_att0 = self.attempt(self.exam, self.students[0], 3)
        self.exam_att1 = self.attempt(self.exam, self.students[1], 8)
        self.rt = self.retake(self.exam, [self.students[0]], start_delta=-120, minutes=30)   # пересдача уже закончилась
        ids = [self.a_tasks[2].id, self.b_tasks[2].id]
        self.retake_att0 = self.attempt(self.exam, self.students[0], 9, task_ids=ids, retake=self.rt)

    def test_grades_are_independent(self):
        self.assertEqual(self.exam_att0.grade(), 3.0)
        self.assertEqual(self.retake_att0.grade(), 9.0)

    def test_student_sees_both_grades(self):
        self.client.force_login(self.students[0].user)
        page = self.client.get(reverse('exam_detail', args=[self.exam.id])).content.decode()
        self.assertIn('3.0/10', page)
        self.assertIn('9.0/10', page)
        self.assertIn('Оценка за пересдачу', page)
        page2 = self.client.get(reverse('retake_detail', args=[self.exam.id, self.rt.id])).content.decode()
        self.assertIn('3.0/10', page2)
        self.assertIn('9.0/10', page2)
        lst = self.client.get(reverse('exam_list')).content.decode()
        self.assertIn('3.0/10', lst)
        self.assertIn('9.0/10', lst)

    def test_student_without_retake_sees_only_exam_grade(self):
        self.client.force_login(self.students[1].user)
        page = self.client.get(reverse('exam_detail', args=[self.exam.id])).content.decode()
        self.assertIn('8.0/10', page)
        self.assertNotIn('Оценка за пересдачу', page)
        lst = self.client.get(reverse('exam_list')).content.decode()
        self.assertNotIn('🔁', lst)

    def test_teacher_results_show_second_grade_only_for_retaking_students(self):
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse('exam_results', args=[self.exam.id]))
        rows = {r['student'].id: r for r in resp.context['rows']}
        self.assertEqual(rows[self.students[0].id]['grade'], 3.0)
        self.assertEqual(rows[self.students[0].id]['retake_grade'], 9.0)
        self.assertEqual(rows[self.students[1].id]['grade'], 8.0)
        self.assertIsNone(rows[self.students[1].id]['retake'])
        self.assertNotIn('retake_grade', rows[self.students[1].id])
        self.assertContains(resp, 'Пересдача')

    def test_teacher_retake_results_page(self):
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse('retake_results', args=[self.exam.id, self.rt.id]))
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['rows']
        self.assertEqual([r['student'] for r in rows], [self.students[0]])
        self.assertEqual(rows[0]['grade'], 9.0)
        self.assertEqual(rows[0]['exam_grade'], 3.0)

    def test_second_grade_hidden_until_retake_finished(self):
        active = self.retake(self.random_exam, [self.students[2]], start_delta=-5, minutes=60)
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse('exam_results', args=[self.random_exam.id]))
        row = [r for r in resp.context['rows'] if r['student'].id == self.students[2].id][0]
        self.assertIsNone(row['retake_grade'])
        self.client.force_login(self.students[2].user)
        page = self.client.get(reverse('exam_detail', args=[self.random_exam.id])).content.decode()
        self.assertIn('Перейти к пересдаче', page)

    def test_retake_without_attempt_counts_zero_after_end(self):
        rt = self.retake(self.exam, [self.students[2]], start_delta=-120, minutes=30)   # не пришёл
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse('exam_results', args=[self.exam.id]))
        row = [r for r in resp.context['rows'] if r['student'].id == self.students[2].id][0]
        self.assertEqual(row['retake_grade'], 0)

    def test_kazakh_pages_render(self):
        self.client.force_login(self.students[0].user)
        session = self.client.session
        session['content_lang'] = 'kk'
        session.save()
        self.assertEqual(self.client.get(reverse('exam_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('retake_detail', args=[self.exam.id, self.rt.id])).status_code, 200)


class KanbanRemovedTests(TestCase):
    """Kanban-доски и лимита «не больше 5 задач в работе» больше нет."""

    def setUp(self):
        cache.clear()
        self.tag = Tag.objects.create(name='циклы')
        self.tasks = [make_task(f'Задача {i}', 'A', self.tag) for i in range(8)]
        user = User.objects.create_user('pupil', password='x')
        self.student = Student.objects.create(user=user, full_name='Ученик', school_class='7F')
        self.client.force_login(user)

    def test_student_can_open_more_than_five_tasks(self):
        for task in self.tasks:
            resp = self.client.get(reverse('task_detail', args=[task.id]))
            self.assertEqual(resp.status_code, 200)
        from .models import Submission
        self.assertEqual(Submission.objects.filter(student=self.student, status='IN_PROGRESS').count(), 8)

    def test_kanban_page_is_gone(self):
        self.assertEqual(self.client.get('/kanban/').status_code, 404)

    def test_no_kanban_link_in_menu(self):
        page = self.client.get(reverse('all_tasks')).content.decode()
        self.assertNotIn('Kanban', page)
        self.assertNotIn('kanban', page)

    def test_exam_text_no_longer_mentions_kanban(self):
        exam = Exam.objects.create(kind='SOR', title='СОР', school_class='7F',
                                   start_at=timezone.now() + timedelta(minutes=-1), duration_minutes=40)
        exam.tasks.set(self.tasks[:2])
        page = self.client.get(reverse('exam_detail', args=[exam.id])).content.decode()
        self.assertIn('Курсы и Все задачи', page)
        self.assertNotIn('Kanban', page)


class RegistrationClosedTests(TestCase):
    def test_closed_by_default(self):
        self.assertEqual(self.client.get('/register/').status_code, 302)
        r = self.client.post('/register/', {'username': 'new1', 'password': 'Passw0rd!x', 'full_name': 'A B', 'school_class': '7F'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(User.objects.filter(username='new1').exists())
        page = self.client.get('/login/').content.decode()
        self.assertNotIn('href="/register/"', page)

    def test_open_when_enabled(self):
        with self.settings(REGISTRATION_OPEN=True):
            self.assertEqual(self.client.get('/register/').status_code, 200)
            self.assertIn('href="/register/"', self.client.get('/login/').content.decode())


class FastSolveBlockTests(TestCase):
    def setUp(self):
        self.teacher_u = User.objects.create_user('ft', password='x', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.teacher_u, full_name='T', classes='7F')
        self.su = User.objects.create_user('fs', password='pw12345')
        self.student = Student.objects.create(user=self.su, full_name='Быстрый', school_class='7F')
        self.tasks = [Task.objects.create(title=f'T{i}', description='d', level='A', test_cases=[]) for i in range(3)]

    def solve(self, task, seconds, score=10):
        from .models import Attempt, Submission
        sub = Submission.objects.create(student=self.student, task=task, status='DONE', score=score)
        Attempt.objects.create(submission=sub, number=1, code='x', score=score,
                               submitted_at=sub.created_at + timedelta(seconds=seconds))

    def test_flagged_only_when_enough_fast(self):
        self.solve(self.tasks[0], 10); self.solve(self.tasks[1], 12)
        self.client.login(username='ft', password='x')
        self.assertNotIn('Быстрый', self.client.get('/teacher/suspicious/').content.decode())
        self.solve(self.tasks[2], 14)
        html = self.client.get('/teacher/suspicious/').content.decode()
        self.assertIn('Быстрый', html)
        self.assertIn('посмотреть код', html)                                   # код быстрых решений можно открыть
        self.assertIn('<pre>x</pre>', html)
        from .models import Submission
        sub = Submission.objects.filter(student=self.student).first()
        back = f'/teacher/suspicious/#s{self.student.id}'
        self.assertIn(f'?back=/teacher/suspicious/%23s{self.student.id}', html)
        page = self.client.get(f'/teacher/grade/{sub.id}/', {'back': back}).content.decode()
        self.assertIn('Назад к подозрительным', page)
        r = self.client.post(f'/teacher/grade/{sub.id}/', {'score': 0, 'comment': 'ИИ', 'back': back})
        self.assertEqual(r['Location'], back)                                    # после сохранения — туда же, где были
        r = self.client.post(f'/teacher/grade/{sub.id}/', {'score': 0, 'comment': 'x', 'back': 'https://evil.example/'})
        self.assertEqual(r['Location'], '/teacher/')                             # чужие адреса не принимаем

    def test_slow_or_wrong_not_counted(self):
        self.solve(self.tasks[0], 60); self.solve(self.tasks[1], 10, score=4); self.solve(self.tasks[2], 5)
        self.client.login(username='ft', password='x')
        self.assertNotIn('Быстрый', self.client.get('/teacher/suspicious/').content.decode())

    def test_block_and_unblock(self):
        self.client.login(username='ft', password='x')
        self.client.post(f'/teacher/students/{self.student.id}/block/')
        self.su.refresh_from_db(); self.assertFalse(self.su.is_active)
        self.client.logout()
        r = self.client.post('/login/', {'username': 'fs', 'password': 'pw12345'})
        self.assertIn('заблокирован', r.content.decode())
        self.client.login(username='ft', password='x')
        self.client.post(f'/teacher/students/{self.student.id}/block/')
        self.su.refresh_from_db(); self.assertTrue(self.su.is_active)

    def test_other_class_and_student_forbidden(self):
        other = Student.objects.create(user=User.objects.create_user('o', password='x'), full_name='O', school_class='9A')
        self.client.login(username='ft', password='x')
        self.client.post(f'/teacher/students/{other.id}/block/')
        other.user.refresh_from_db(); self.assertTrue(other.user.is_active)
        self.client.logout(); self.client.login(username='fs', password='pw12345')
        self.assertEqual(self.client.get('/teacher/suspicious/').status_code, 302)


class ActivitySignalTests(TestCase):
    def setUp(self):
        self.teacher_u = User.objects.create_user('at', password='x', is_staff=True)
        Teacher.objects.create(user=self.teacher_u, full_name='T', classes='7F')
        self.su = User.objects.create_user('as', password='pw12345')
        self.student = Student.objects.create(user=self.su, full_name='Тихий', school_class='7F')
        self.task = Task.objects.create(title='T', description='d', level='A', test_cases=[])

    def page(self):
        self.client.login(username='at', password='x')
        html = self.client.get('/teacher/suspicious/').content.decode()
        self.client.logout()
        return html

    def test_idle_attempts_flagged_and_counts_saved(self):
        self.client.login(username='as', password='pw12345')
        url = f'/task/{self.task.id}/'
        self.client.get(url)
        r = self.client.post(url, {'code': 'print(1)', 'key_count': '0', 'pointer_count': '0'})
        self.assertEqual(r.status_code, 302)
        from .models import Attempt, Submission
        a = Attempt.objects.get()
        self.assertEqual((a.key_count, a.pointer_count), (0, 0))
        self.client.logout()
        self.assertNotIn('Тихий', self.page())   # одной отправки мало
        sub = Submission.objects.get()
        for n in (2, 3):
            Attempt.objects.create(submission=sub, number=n, code='x', submitted_at=timezone.now(), key_count=0, pointer_count=0)
        self.assertIn('Тихий', self.page())

    def test_missing_counts_not_flagged(self):
        from .models import Attempt, Submission
        sub = Submission.objects.create(student=self.student, task=self.task)
        for n in (1, 2, 3):
            Attempt.objects.create(submission=sub, number=n, code='x', submitted_at=timezone.now())
        self.assertNotIn('Тихий', self.page())

    def test_login_logged_and_automation_flagged(self):
        from .models import LoginEvent
        self.client.post('/login/', {'username': 'as', 'password': 'pw12345'}, HTTP_USER_AGENT='Mozilla HeadlessChrome/120')
        self.assertEqual(LoginEvent.objects.count(), 1)
        self.assertIn('автоматизированного', self.page())

    def test_many_devices_is_not_suspicious(self):
        from .models import LoginEvent
        for ua in ('A', 'B', 'C', 'D'):
            LoginEvent.objects.create(user=self.su, user_agent=ua)
        self.assertNotIn('устройств', self.page())                       # вход с разных устройств — не признак


class TemporaryBlockTests(TestCase):
    setUp = FastSolveBlockTests.setUp
    """Те же данные, что в FastSolveBlockTests (наследуем setUp)."""

    def test_temp_block_72h_then_auto_release(self):
        from django.utils import timezone as tz
        self.client.login(username='ft', password='x')
        self.client.post(f'/teacher/students/{self.student.id}/block/', {'duration': 'temp'})
        self.student.refresh_from_db(); self.su.refresh_from_db()
        self.assertFalse(self.su.is_active)
        left = self.student.blocked_until - tz.now()
        self.assertTrue(timedelta(hours=71, minutes=59) < left <= timedelta(hours=72))
        self.client.logout()
        r = self.client.post('/login/', {'username': 'fs', 'password': 'pw12345'})
        self.assertIn('заблокирован до', r.content.decode())
        Student.objects.filter(id=self.student.id).update(blocked_until=tz.now() - timedelta(minutes=1))
        r = self.client.post('/login/', {'username': 'fs', 'password': 'pw12345'})
        self.assertEqual(r.status_code, 302)
        self.su.refresh_from_db(); self.assertTrue(self.su.is_active)

    def test_forever_block_stays(self):
        self.client.login(username='ft', password='x')
        self.client.post(f'/teacher/students/{self.student.id}/block/', {'duration': 'forever'})
        self.student.refresh_from_db()
        self.assertIsNone(self.student.blocked_until)
        self.client.get('/teacher/students/')
        self.su.refresh_from_db(); self.assertFalse(self.su.is_active)


class EasyWhileTasksMigrationTests(TestCase):
    def test_easy_tasks_added(self):
        from .autotest import run_autotests
        easy = Task.objects.filter(tags__name='while', level='A', title_kk__gt='')
        self.assertEqual(easy.count(), 15)
        task = Task.objects.get(title='Сумма чисел от 1 до N')
        ok = run_autotests(task, 'n=int(input())\ni=1\ns=0\nwhile i<=n:\n    s+=i\n    i+=1\nprint(s)')
        self.assertEqual((ok['passed'], ok['total']), (5, 5))

    def test_sandbox_all_reference_outputs_match_task_text_examples(self):
        for t in Task.objects.filter(tags__name='while', level='A', title_kk__gt=''):
            self.assertTrue(t.input_example and t.output_example and len(t.test_cases) >= 4, t.title)


class WhileInputPerLineTests(TestCase):
    def test_per_line_conversion(self):
        import importlib
        mod = importlib.import_module('contest.migrations.0029_while_input_per_line')
        self.assertEqual(mod.per_line('10 20 5 -1'), '10\n20\n5\n-1')
        self.assertEqual(mod.per_line('abc, password, pass1234'), 'abc\npassword\npass1234')
        self.assertEqual(mod.per_line('7\n3 5 9 7 2', keep_first_line=True), '7\n3\n5\n9\n7\n2')
        self.assertEqual(mod.per_line('qwerty12345'), 'qwerty12345')

    def test_migration_updates_task(self):
        import importlib
        from django.apps import apps
        mod = importlib.import_module('contest.migrations.0029_while_input_per_line')
        tag, _ = Tag.objects.get_or_create(name='while')
        t = Task.objects.create(title='Сумма до отрицательного числа', level='C', description='Условие.',
                                input_example='10 20 5 -1', output_example='35',
                                test_cases=[{'input': '1 2 -3', 'output': '3'}])
        t.tags.add(tag)
        mod.forward(apps, None)
        t.refresh_from_db()
        self.assertEqual(t.input_example, '10\n20\n5\n-1')
        self.assertEqual(t.test_cases[0]['input'], '1\n2\n-3')
        self.assertIn('отдельной строке', t.description)


class HardWhileTasksTests(TestCase):
    def test_five_hard_tasks_with_reference_solutions(self):
        from .autotest import run_autotests
        hard = Task.objects.filter(tags__name='while', level='C', title_kk__gt='')
        self.assertEqual(hard.count(), 5)
        sols = {
            'Следующее простое число': 'n=int(input())+1\nwhile True:\n    d=2\n    ok=n>1\n    while d*d<=n:\n        if n%d==0:\n            ok=False\n            break\n        d+=1\n    if ok:\n        break\n    n+=1\nprint(n)',
            'Банкомат: наименьшее число купюр': 'n=int(input())\nc=0\nfor b in (10000,5000,2000,1000,500,200,100):\n    while n>=b:\n        n-=b\n        c+=1\nprint(c)',
            'Максимум и его позиция': 'm=0\np=0\ni=0\nx=int(input())\nwhile x!=0:\n    i+=1\n    if x>m:\n        m=x\n        p=i\n    x=int(input())\nprint(m)\nprint(p)',
            'Три попытки ввода пароля': 'pw=input()\nk=1\nwhile k<=3:\n    if input()==pw:\n        print("Вход выполнен с попытки", k)\n        break\n    k+=1\nelse:\n    print("Доступ заблокирован")',
            'Первое число Фибоначчи больше N': 'n=int(input())\na=1\nb=1\nwhile b<=n:\n    a,b=b,a+b\nprint(b)',
        }
        for title, code in sols.items():
            r = run_autotests(Task.objects.get(title=title), code)
            self.assertEqual(r['passed'], r['total'], title)


class BadgeTests(TestCase):
    def setUp(self):
        from .models import Submission
        self.user = User.objects.create_user('bs', password='x')
        self.student = Student.objects.create(user=self.user, full_name='Бадж', school_class='7F')
        self.teacher_u = User.objects.create_user('bt', password='x', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.teacher_u, full_name='T', classes='7F')
        self.tasks = [Task.objects.create(title=f'B{i}', description='d', level='A', test_cases=[]) for i in range(12)]

    def solve(self, n, score=10):
        from .models import Submission
        for t in self.tasks[:n]:
            Submission.objects.update_or_create(student=self.student, task=t, defaults={'status': 'DONE', 'score': score, 'is_graded': True})

    def exam_with_grade(self, kind, hours_ago, grade_score, retake=False, exam=None):
        now = timezone.now()
        if exam is None:
            exam = Exam.objects.create(kind=kind, title='E', school_class='7F', start_at=now - timedelta(hours=hours_ago),
                                       duration_minutes=40, created_by=self.teacher)
            exam.tasks.set(self.tasks[:2])
        rt = None
        ids = []
        if retake:
            rt = ExamRetake.objects.create(exam=exam, start_at=now - timedelta(hours=hours_ago - 1),
                                           duration_minutes=30, created_by=self.teacher)
            rt.students.add(self.student)
            ids = [t.id for t in self.tasks[2:4]]
        a = ExamAttempt.objects.create(exam=exam, student=self.student, retake=rt, task_ids=ids,
                                       started_at=now - timedelta(hours=hours_ago), finished_at=now - timedelta(hours=hours_ago - 0.5))
        for t in a.task_list():
            ExamAnswer.objects.create(attempt=a, task=t, best_score=grade_score, submissions_count=1)
        return exam

    def keys(self):
        from .badges import compute
        return set(compute(self.student)[0])

    def test_task_badges_thresholds(self):
        self.assertEqual(self.keys(), set())
        self.solve(9); self.assertNotIn('t10', self.keys())
        self.solve(10); self.assertEqual(self.keys(), {'t10'})
        self.solve(12, score=9)   # перезапись на 9 — решённых 0
        self.assertEqual(self.keys(), set())

    def test_exam_badges(self):
        self.exam_with_grade('SOR', 30, 10)
        self.assertEqual(self.keys(), {'e_first', 'e_sor'})
        self.exam_with_grade('SOCH', 20, 10)
        self.assertEqual(self.keys(), {'e_first', 'e_sor', 'e_soch'})
        self.exam_with_grade('SOR', 10, 10)
        self.assertIn('e_streak', self.keys())

    def test_streak_broken_by_low_grade_not_by_absence(self):
        self.exam_with_grade('SOR', 40, 10)
        self.exam_with_grade('SOR', 30, 5)
        self.exam_with_grade('SOR', 20, 10)
        self.exam_with_grade('SOR', 10, 10)
        self.assertNotIn('e_streak', self.keys())

    def test_fixed_by_retake_and_five_tens(self):
        e = self.exam_with_grade('SOR', 30, 4)
        self.assertNotIn('e_fixed', self.keys())
        self.exam_with_grade('SOR', 30, 9, retake=True, exam=e)
        self.assertIn('e_fixed', self.keys())
        self.assertNotIn('e_first', self.keys())
        for h in (60, 50, 40, 25, 15):
            self.exam_with_grade('SOR', h, 10)
        self.assertIn('e_five', self.keys())

    def test_profile_shows_badges_modal_text_and_marks_seen(self):
        from .models import StudentBadge
        self.solve(10)
        self.client.login(username='bs', password='x')
        html = self.client.get('/profile/').content.decode()
        self.assertIn('Intern', html)
        self.assertIn('Решите 10 задач на оценку 10/10.', html)
        self.assertIn('id="badge-toast"', html)       # новый значок — всплывашка
        self.assertTrue(StudentBadge.objects.get(student=self.student, key='t10').seen)
        self.assertNotIn('id="badge-toast"', self.client.get('/profile/').content.decode())  # второй раз не показываем
        s = self.client.session; s['content_lang'] = 'kk'; s.save()
        kz = self.client.get('/profile/').content.decode()
        self.assertIn('Intern', kz)
        self.assertIn('10/10 бағамен 10 есеп шешіңіз.', kz)

    def test_badge_date_is_when_condition_met(self):
        from .models import Submission, StudentBadge
        from .badges import sync_badges
        self.solve(10)
        old = timezone.now() - timedelta(days=30)
        Submission.objects.filter(student=self.student).update(updated_at=old)
        sync_badges(self.student)
        b = StudentBadge.objects.get(student=self.student, key='t10')
        self.assertLess(abs((b.awarded_at - old).total_seconds()), 5)


class FailedTestDetailsTests(TestCase):
    def setUp(self):
        u = User.objects.create_user('fd', password='pw12345')
        self.student = Student.objects.create(user=u, full_name='Ученик', school_class='7F')
        self.task = Task.objects.create(
            title='Сумма', description='d', level='A',
            test_cases=[{'input': '2\n3', 'output': '5'}, {'input': '7\n8', 'output': '15'}])
        self.client.login(username='fd', password='pw12345')

    def submit(self, code):
        return self.client.post(reverse('task_detail', args=[self.task.id]), {'code': code}, follow=True)

    def test_wrong_answer_shows_input_and_own_output_not_expected(self):
        r = self.submit('a=int(input());b=int(input());print(a*b+1000)')
        html = r.content.decode()
        self.assertIn('Не прошёл тест', html)
        self.assertIn('Ваш вывод', html)
        self.assertIn('1006', html)         # вывод ученика на первом тесте
        self.assertIn('Ожидалось', html)
        self.assertIn('>5</pre>', html)     # ожидаемый ответ показан

    def test_runtime_error_shown(self):
        r = self.submit('print(1/0)')
        self.assertIn('Деление на ноль', r.content.decode())

    def test_all_passed_no_failure_block(self):
        r = self.submit('a=int(input());b=int(input());print(a+b)')
        self.assertNotIn('Не прошёл тест', r.content.decode())


class ErrorHintTests(FailedTestDetailsTests):
    def test_hint_ru_and_kk(self):
        r = self.submit('print(1/0)')
        self.assertIn('Деление на ноль', r.content.decode())
        s = self.client.session; s['content_lang'] = 'kk'; s.save()
        r = self.client.get(reverse('task_detail', args=[self.task.id]))
        self.assertIn('Нөлге бөлу', r.content.decode())


class OldSubmissionInputFallbackTests(FailedTestDetailsTests):
    def test_old_submission_without_input_uses_task_case(self):
        from .models import Submission
        sub = Submission.objects.create(
            student=self.student, task=self.task, status='TESTING', code='print(0)',
            autotest_checked_at=timezone.now(), autotest_passed=0, autotest_total=2,
            autotest_results=[{'ok': False, 'error': None, 'expected': '5', 'actual': '0'}])
        r = self.client.get(reverse('task_detail', args=[self.task.id]))
        self.assertIn('Входные данные', r.content.decode())


class EmptyExpectedTests(TestCase):
    def test_empty_expected_shown(self):
        u = User.objects.create_user('ee', password='pw12345')
        st = Student.objects.create(user=u, full_name='Э', school_class='7F')
        task = Task.objects.create(title='Пусто', description='d', level='A',
                                   test_cases=[{'input': '1', 'output': ''}])
        self.client.login(username='ee', password='pw12345')
        r = self.client.post(reverse('task_detail', args=[task.id]), {'code': 'print(1)'}, follow=True)
        html = r.content.decode()
        self.assertIn('Ожидалось', html)
        self.assertIn('(пусто)', html)


class TeacherSolveTests(TestCase):
    def setUp(self):
        from .models import Submission
        self.Submission = Submission
        u = User.objects.create_user('tt', password='pw12345')
        Teacher.objects.create(user=u, full_name='Учитель', classes='7F')
        self.task = Task.objects.create(
            title='Сумма', description='d', level='A',
            test_cases=[{'input': '2\n3', 'output': '5'}, {'input': '7\n8', 'output': '15'}])
        self.client.login(username='tt', password='pw12345')
        self.url = reverse('task_detail', args=[self.task.id])

    def test_form_visible_for_teacher(self):
        r = self.client.get(self.url)
        self.assertIn('Решить как преподаватель', r.content.decode())

    def test_teacher_run_shows_result_and_saves_nothing(self):
        r = self.client.post(self.url, {'code': 'a=int(input());b=int(input());print(a*b)'})
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn('Не прошёл тест', html)
        self.assertIn('Ожидалось', html)
        self.assertEqual(self.Submission.objects.count(), 0)

    def test_teacher_all_pass(self):
        r = self.client.post(self.url, {'code': 'a=int(input());b=int(input());print(a+b)'})
        html = r.content.decode()
        self.assertIn('2/2', html)
        self.assertNotIn('Не прошёл тест', html)


class InputHelpTests(TestCase):
    """Подсказка про ввод данных — только после ValueError/EOFError, а не всегда."""
    def setUp(self):
        from .models import Submission
        self.Submission = Submission
        u = User.objects.create_user('ih', password='pw12345')
        self.st = Student.objects.create(user=u, full_name='И', school_class='7F')
        self.client.login(username='ih', password='pw12345')

    def page(self, example, error=None, lang=None):
        t = Task.objects.create(title='X', description='d', level='A', input_example=example,
                                test_cases=[{'input': '3\n7', 'output': '10'}])
        if error:
            self.Submission.objects.create(
                student=self.st, task=t, status='DONE', score=0, is_graded=True,
                autotest_checked_at=timezone.now(), autotest_total=1, autotest_passed=0,
                autotest_results=[{'ok': False, 'error': error, 'input': '3 7'}])
        if lang:
            s = self.client.session; s['content_lang'] = lang; s.save()
        return self.client.get(reverse('task_detail', args=[t.id])).content.decode()

    def test_hidden_without_error(self):
        self.assertNotIn('Обратите внимание на ввод данных', self.page('3\n7'))

    def test_hidden_for_other_errors(self):
        self.assertNotIn('Обратите внимание на ввод данных', self.page('3\n7', 'ZeroDivisionError: x'))

    def test_lines(self):
        h = self.page('3\n7', 'ValueError: invalid literal')
        self.assertIn('Обратите внимание на ввод данных', h)
        self.assertIn('на РАЗНЫХ строках', h)
        self.assertNotIn('input().split()))', h)
        self.assertNotIn('ValueError: invalid', h)

    def test_one_line_suggests_map(self):
        h = self.page('3 7', 'ValueError: x')
        self.assertIn('в ОДНОЙ строке', h)
        self.assertIn('map(int, input().split())', h)

    def test_eof_and_kk(self):
        self.assertIn('Кіріс деректеріне', self.page('3 7', 'EOFError: EOF', 'kk'))


class TaskStatusFilterTests(TestCase):
    def setUp(self):
        from .models import Submission
        u = User.objects.create_user('sf', password='pw12345')
        st = Student.objects.create(user=u, full_name='Ф', school_class='7F')
        self.done = Task.objects.create(title='Сделанная', description='d', level='A', test_cases=[])
        self.work = Task.objects.create(title='Рабочая', description='d', level='A', test_cases=[])
        self.new = Task.objects.create(title='Новая', description='d', level='B', test_cases=[])
        Submission.objects.create(student=st, task=self.done, status='DONE', score=10, is_graded=True)
        Submission.objects.create(student=st, task=self.work, status='IN_PROGRESS')
        self.partial = Task.objects.create(title='Девятка', description='d', level='A', test_cases=[])
        Submission.objects.create(student=st, task=self.partial, status='DONE', score=9, is_graded=True)
        self.client.login(username='sf', password='pw12345')

    def titles(self, qs):
        h = self.client.get('/' + qs).content.decode()
        return {n for n in ('Сделанная', 'Рабочая', 'Новая', 'Девятка') if f'>{n}<' in h or n in h}

    def test_filters(self):
        self.assertEqual(self.titles('?status=done'), {'Сделанная'})
        self.assertEqual(self.titles('?status=progress'), {'Рабочая', 'Девятка'})
        self.assertEqual(self.titles('?status=unsolved'), {'Рабочая', 'Новая', 'Девятка'})
        self.assertEqual(self.titles('?all=1'), {'Сделанная', 'Рабочая', 'Новая', 'Девятка'})

    def test_combines_with_level_and_keeps_params_in_links(self):
        self.assertEqual(self.titles('?status=unsolved&level=B'), {'Новая'})
        h = self.client.get('/?status=done').content.decode()
        self.assertIn('status=done', h)
        self.assertIn('level=A&amp;status=done', h)

    def test_invalid_status_ignored(self):
        self.assertEqual(self.titles('?status=zzz'), {'Сделанная', 'Рабочая', 'Новая', 'Девятка'})


class TwoRetakesPagesTests(RetakeTestBase):
    def test_pages_render_with_two_retakes(self):
        st = self.students[0]
        self.attempt(self.exam, st, 3, task_ids=[self.a_tasks[0].id, self.b_tasks[0].id])
        rt1 = self.retake(self.exam, [st], start_delta=-120, minutes=30)
        self.attempt(self.exam, st, 6, retake=rt1, task_ids=[self.a_tasks[1].id, self.b_tasks[1].id])
        rt2 = self.retake(self.exam, [st], start_delta=-5, minutes=60)
        self.client.force_login(st.user)
        r = self.client.get(reverse('exam_detail', args=[self.exam.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('Предыдущая пересдача', r.content.decode())
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(reverse('exam_results', args=[self.exam.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse('retake_create', args=[self.exam.id])).status_code, 200)


@override_settings(DUELS_ADMIN_USERNAMES=['dt'])
class DuelTests(TestCase):
    def setUp(self):
        from . import duels
        self.duels = duels
        self.teacher_u = User.objects.create_user('dt', password='x', is_staff=True)
        Teacher.objects.create(user=self.teacher_u, full_name='Т', classes='7F')
        self.users = []
        self.st = []
        for name in ('a', 'b', 'c'):
            u = User.objects.create_user(f'd{name}', password='pw12345')
            self.users.append(u)
            self.st.append(Student.objects.create(user=u, full_name=f'Игрок {name}', school_class='7F'))
        from .models import SiteSettings
        _s = SiteSettings.get(); _s.set_duel_classes(['7F']); _s.save()     # класс открыт преподавателем
        Task.objects.all().delete()     # задачи из миграций не нужны — берём только свои
        self.tasks = [Task.objects.create(title=f'T{i}', description='d', level='A',
                                          input_example='1', output_example='1',
                                          test_cases=[{'input': '2', 'output': '4'}, {'input': '3', 'output': '9'}])
                      for i in range(4)]
        self.good = 'n=int(input());print(n*n)'
        self.bad = 'print(0)'

    def start(self):
        self.duels.join_queue(self.st[0]); self.duels.join_queue(self.st[1])
        duel = self.duels.try_match(self.st[0])
        self.assertIsNotNone(duel)
        return duel

    # --- Эло
    def test_elo_math(self):
        d = self.duels
        self.assertEqual(d.rating_delta(800, 800, 1), 16)
        self.assertEqual(d.rating_delta(800, 800, 0), -16)
        self.assertEqual(d.rating_delta(800, 800, 0.5), 0)
        self.assertGreater(d.rating_delta(700, 900, 1), 16)     # неожиданная победа — больше очков

    def test_everyone_starts_at_800(self):
        self.assertEqual(self.st[0].duel_rating, 800)

    # --- подбор
    def test_match_creates_duel_with_three_tasks_and_clears_queue(self):
        duel = self.start()
        self.assertEqual(duel.duel_tasks.count(), 3)
        self.assertEqual(DuelQueueEntry.objects.count(), 0)

    def test_far_ratings_do_not_match_immediately(self):
        Student.objects.filter(pk=self.st[1].pk).update(duel_rating=1600)
        self.st[1].refresh_from_db()
        self.duels.join_queue(self.st[0]); self.duels.join_queue(self.st[1])
        self.assertIsNone(self.duels.try_match(self.st[0]))

    def test_levels_by_rating(self):
        self.assertEqual(self.duels.levels_for(800), ['A', 'B'])
        self.assertEqual(self.duels.levels_for(1500), ['A', 'B'])

    # --- игра
    def test_first_full_solution_takes_point_and_closes_task(self):
        duel = self.start()
        dt = duel.duel_tasks.first()
        ok, msg, _ = self.duels.submit_code(duel, self.st[0], dt, self.good)
        self.assertTrue(ok)
        duel.refresh_from_db(); dt.refresh_from_db()
        self.assertEqual(dt.winner_id, self.st[0].id)
        self.assertEqual(duel.points_of(self.st[0]), 1)
        ok, msg, _ = self.duels.submit_code(duel, self.st[1], dt, self.good)   # закрыта
        self.assertFalse(ok)

    def test_three_attempts_limit(self):
        duel = self.start()
        dt = duel.duel_tasks.first()
        for _ in range(3):
            self.assertTrue(self.duels.submit_code(duel, self.st[0], dt, self.bad)[0])
        ok, msg, _ = self.duels.submit_code(duel, self.st[0], dt, self.good)
        self.assertFalse(ok)
        self.assertEqual(self.duels.attempts_left(dt, self.st[0]), 0)

    def test_two_points_finish_and_rating_change(self):
        duel = self.start()
        for dt in list(duel.duel_tasks.all())[:2]:
            self.duels.submit_code(duel, self.st[0], dt, self.good)
        duel.refresh_from_db()
        self.assertEqual(duel.status, 'FINISHED')
        self.assertEqual(duel.winner_id, self.st[0].id)
        for s in self.st[:2]:
            s.refresh_from_db()
        self.assertEqual(self.st[0].duel_rating, 816)
        self.assertEqual(self.st[1].duel_rating, 784)
        self.assertEqual(self.st[0].duel_games, 1)

    def test_resign_gives_win_to_opponent(self):
        duel = self.start()
        self.duels.resign(duel, self.st[0])
        duel.refresh_from_db()
        self.assertEqual(duel.winner_id, self.st[1].id)
        self.assertEqual(duel.end_reason, 'resign')

    def test_all_tasks_exhausted_is_draw_when_points_equal(self):
        duel = self.start()
        for dt in duel.duel_tasks.all():
            for p in self.st[:2]:
                for _ in range(3):
                    self.duels.submit_code(duel, p, dt, self.bad)
        duel.refresh_from_db()
        self.assertEqual(duel.status, 'FINISHED')
        self.assertIsNone(duel.winner_id)

    def test_inactive_duel_closes_by_points(self):
        duel = self.start()
        dt = duel.duel_tasks.first()
        self.duels.submit_code(duel, self.st[1], dt, self.good)
        Duel.objects.filter(pk=duel.pk).update(last_activity=timezone.now() - timedelta(minutes=30))
        duel.refresh_from_db()
        self.duels.close_if_inactive(duel)
        duel.refresh_from_db()
        self.assertEqual(duel.status, 'FINISHED')
        self.assertEqual(duel.winner_id, self.st[1].id)
        self.assertEqual(duel.end_reason, 'inactive')

    def test_finish_is_idempotent(self):
        duel = self.start()
        self.duels.resign(duel, self.st[0]); self.duels.resign(duel, self.st[0])
        self.st[0].refresh_from_db()
        self.assertEqual(self.st[0].duel_games, 1)

    # --- допуск
    def test_disabled_blocks_play(self):
        from .models import SiteSettings
        s = SiteSettings.get(); s.duels_mode = 'off'; s.save()
        self.client.login(username='da', password='pw12345')
        self.client.post(reverse('duels_find'))
        self.assertEqual(DuelQueueEntry.objects.count(), 0)

    def test_default_mode_shows_only_open_classes(self):
        from .models import SiteSettings
        self.assertEqual(SiteSettings.get().duels_mode, 'testers')     # внутреннее имя режима «по классам»
        self.client.login(username='da', password='pw12345')
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)
        self.assertIn('Дуэли', self.client.get(reverse('all_tasks')).content.decode())
        s = SiteSettings.get(); s.set_duel_classes([]); s.save()       # преподаватель закрыл класс
        r = self.client.get(reverse('duels'))
        self.assertRedirects(r, reverse('all_tasks'))
        self.client.post(reverse('duels_find'))
        self.assertEqual(DuelQueueEntry.objects.count(), 0)
        self.assertNotIn('Дуэли', self.client.get(reverse('all_tasks')).content.decode())

    def test_all_mode_opens_for_everyone(self):
        from .models import SiteSettings
        s = SiteSettings.get(); s.duels_mode = 'all'; s.save()
        self.client.login(username='dc', password='pw12345')
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)

    def test_suspicious_student_can_still_play(self):
        from unittest import mock
        with mock.patch('contest.moderation_views._reasons', return_value={self.st[0].id: ['x']}):
            self.client.login(username='da', password='pw12345')
            self.client.post(reverse('duels_find'))
        self.assertEqual(DuelQueueEntry.objects.count(), 1)

    def test_teacher_toggle_cycles_modes(self):
        from .models import SiteSettings
        self.client.login(username='dt', password='x')
        seen = []
        for _ in range(3):
            self.client.post(reverse('duels_toggle'))
            seen.append(SiteSettings.get().duels_mode)
        self.assertEqual(seen, ['all', 'off', 'testers'])

    # --- страницы
    def test_pages_and_full_flow_over_http(self):
        ca, cb = self.client_class(), self.client_class()
        ca.login(username='da', password='pw12345'); cb.login(username='db', password='pw12345')
        self.assertEqual(ca.get(reverse('duels')).status_code, 200)
        ca.post(reverse('duels_find'))
        self.assertIn('Ищем соперника', ca.get(reverse('duels')).content.decode())
        r = cb.post(reverse('duels_find'))                      # второй игрок — сразу пара
        duel = Duel.objects.get()
        self.assertRedirects(r, reverse('duel_match', args=[duel.id]))
        self.assertEqual(ca.get(reverse('duels_status')).json()['state'], 'active')
        page = ca.get(reverse('duel_match', args=[duel.id]))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('Игрок b', html)
        dt = duel.duel_tasks.first()
        ca.post(reverse('duel_submit', args=[duel.id, dt.id]), {'code': self.good})
        self.assertEqual(ca.get(reverse('duel_state', args=[duel.id])).json()['signature'].split('-')[1], '1')
        out = self.client_class(); out.login(username='dc', password='pw12345')
        out.get(reverse('duel_match', args=[duel.id]))          # чужой — редирект, не ошибка
        self.assertEqual(out.get(reverse('duel_state', args=[duel.id])).status_code, 403)
        cb.post(reverse('duel_resign', args=[duel.id]))
        self.assertIn('Победа', ca.get(reverse('duel_match', args=[duel.id])).content.decode())
        self.assertIn('Игрок a', ca.get(reverse('duels')).content.decode())


@override_settings(DUELS_ADMIN_USERNAMES=['tv'])
class DuelsTeacherViewTests(TestCase):
    def test_teacher_sees_link_and_overview_students_do_not(self):
        t = User.objects.create_user('tv', password='x', is_staff=True)
        Teacher.objects.create(user=t, full_name='Т', classes='7F')
        su = User.objects.create_user('sv', password='x')
        Student.objects.create(user=su, full_name='Обычный', school_class='7F')
        self.client.login(username='tv', password='x')
        self.assertIn('/duels/', self.client.get(reverse('all_tasks')).content.decode())
        page = self.client.get(reverse('duels')).content.decode()
        self.assertIn('Для преподавателя', page)
        self.assertIn('по классам', page)
        self.client.login(username='sv', password='x')
        self.assertNotIn('/duels/', self.client.get(reverse('all_tasks')).content.decode())
        self.assertRedirects(self.client.get(reverse('duels')), reverse('all_tasks'))


class DuelSequentialAndLeaveTests(DuelTests):
    def test_only_current_task_accepts_code(self):
        duel = self.start()
        t1, t2, t3 = list(duel.duel_tasks.all())
        ok, msg, _ = self.duels.submit_code(duel, self.st[0], t2, self.good)
        self.assertFalse(ok)
        self.assertIn('не открыта', msg)
        self.assertTrue(self.duels.submit_code(duel, self.st[0], t1, self.good)[0])
        self.assertEqual(self.duels.current_task(duel).id, t2.id)       # решили — сразу следующая
        self.assertTrue(self.duels.submit_code(duel, self.st[1], t2, self.bad)[0])

    def test_next_task_opens_when_attempts_exhausted(self):
        duel = self.start()
        t1, t2, _t3 = list(duel.duel_tasks.all())
        for p in self.st[:2]:
            for _ in range(3):
                self.duels.submit_code(duel, p, t1, self.bad)
        self.assertEqual(self.duels.current_task(duel).id, t2.id)

    def test_future_tasks_hidden_on_page(self):
        duel = self.start()
        self.client.login(username='da', password='pw12345')
        html = self.client.get(reverse('duel_match', args=[duel.id])).content.decode()
        self.assertIn('откроется после предыдущей', html)
        self.assertEqual(html.count('откроется после предыдущей'), 2)

    def test_leaving_page_is_instant_loss(self):
        duel = self.start()
        self.client.login(username='da', password='pw12345')
        self.assertEqual(self.client.post(reverse('duel_leave', args=[duel.id])).status_code, 200)
        duel.refresh_from_db()
        self.assertEqual(duel.status, 'FINISHED')
        self.assertEqual(duel.winner_id, self.st[1].id)
        self.assertEqual(duel.end_reason, 'left')
        self.st[0].refresh_from_db()
        self.assertEqual(self.st[0].duel_rating, 784)

    def test_stranger_cannot_make_others_leave(self):
        duel = self.start()
        self.client.login(username='dc', password='pw12345')
        self.client.post(reverse('duel_leave', args=[duel.id]))
        duel.refresh_from_db()
        self.assertEqual(duel.status, 'ACTIVE')


class DuelTimeLimitTests(DuelTests):
    def age(self, duel, minutes):
        """Сдвигает начало дуэли назад — как будто прошло minutes минут."""
        shift = timedelta(minutes=minutes)
        Duel.objects.filter(pk=duel.pk).update(created_at=duel.created_at - shift, last_activity=timezone.now())
        duel.duel_tasks.filter(opened_at__isnull=False).update(opened_at=timezone.now() - shift)

    def test_task_times_out_after_five_minutes_and_next_opens(self):
        duel = self.start()
        t1, t2, t3 = list(duel.duel_tasks.all())
        self.age(duel, 6)
        duel = self.duels.refresh(duel)
        t1.refresh_from_db()
        self.assertTrue(t1.timed_out)
        self.assertIsNone(t1.winner_id)
        self.assertEqual(self.duels.current_task(duel).id, t2.id)
        ok, msg, _ = self.duels.submit_code(duel, self.st[0], t1, self.good)     # старая уже закрыта
        self.assertFalse(ok)
        self.assertTrue(self.duels.submit_code(duel, self.st[0], t2, self.good)[0])

    def test_each_task_gets_its_own_five_minutes(self):
        duel = self.start()
        t1, t2, _t3 = list(duel.duel_tasks.all())
        self.duels.submit_code(duel, self.st[0], t1, self.good)                  # решили сразу
        t2.refresh_from_db()
        self.assertIsNotNone(t2.opened_at)
        self.assertGreater(self.duels.seconds_left(t2), 290)

    def test_all_tasks_timed_out_ends_in_draw(self):
        duel = self.start()
        self.age(duel, 20)
        duel = self.duels.refresh(duel)
        self.assertEqual(duel.status, 'FINISHED')
        self.assertIsNone(duel.winner_id)

    def test_timer_shown_on_page(self):
        duel = self.start()
        self.client.login(username='da', password='pw12345')
        html = self.client.get(reverse('duel_match', args=[duel.id])).content.decode()
        self.assertIn('duel-timer', html)


class CodeEditorPartialTests(DuelTests):
    def test_editor_partial_has_no_stray_markup(self):
        duel = self.start()
        self.client.login(username='da', password='pw12345')
        html = self.client.get(reverse('duel_match', args=[duel.id])).content.decode()
        self.assertIn('codemirror.min.js', html)
        self.assertNotIn('{#', html)
        self.assertNotIn('Подключается один раз', html)
        self.assertEqual(html.count('<textarea'), 1)


@override_settings(DUELS_ADMIN_USERNAMES=['boss'])
class DuelsAdminOnlyTests(TestCase):
    def setUp(self):
        for name in ('boss', 'other'):
            u = User.objects.create_user(name, password='x', is_staff=True)
            Teacher.objects.create(user=u, full_name=name, classes='7F')

    def test_other_teacher_sees_page_but_cannot_switch_global_mode(self):
        from .models import SiteSettings
        self.client.login(username='other', password='x')
        self.assertIn('/duels/', self.client.get(reverse('all_tasks')).content.decode())
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)
        self.client.post(reverse('duels_toggle'))
        self.assertEqual(SiteSettings.get().duels_mode, 'testers')            # общий режим не переключился
        self.assertNotIn('Дуэли:', self.client.get(reverse('teacher_dashboard')).content.decode())

    def test_admin_teacher_sees_and_controls(self):
        from .models import SiteSettings
        self.client.login(username='boss', password='x')
        self.assertIn('/duels/', self.client.get(reverse('all_tasks')).content.decode())
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)
        self.assertNotIn('Дуэли:', self.client.get(reverse('teacher_dashboard')).content.decode())   # кнопки в кабинете больше нет
        self.assertIn('Переключить режим', self.client.get(reverse('duels')).content.decode())        # переключатель — на странице «Дуэли»
        self.client.post(reverse('duels_toggle'))
        self.assertEqual(SiteSettings.get().duels_mode, 'all')

class CourseClassVisibilityTests(TestCase):
    def setUp(self):
        self.tu = User.objects.create_user('tc', password='p', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.tu, full_name='Нур Омарбек', classes='7F, 8A')
        self.s7 = User.objects.create_user('s7', password='p')
        Student.objects.create(user=self.s7, full_name='A', school_class='7F')
        self.s9 = User.objects.create_user('s9', password='p')
        Student.objects.create(user=self.s9, full_name='B', school_class='9A')

    def test_teacher_creates_course_for_chosen_classes(self):
        self.client.login(username='tc', password='p')
        page = self.client.get(reverse('course_create'))
        self.assertContains(page, 'value="7F"')
        self.assertContains(page, 'value="8A"')
        self.client.post(reverse('course_create'), {
            'name': 'Python', 'is_visible': 'on', 'visible_classes': ['7F', '9A']})
        course = Course.objects.get(name='Python')
        # 9A не класс этого учителя — отбрасывается
        self.assertEqual(course.visible_class_list(), ['7F'])
        self.assertEqual(course.created_by, self.teacher)

    def test_students_see_only_their_class_courses(self):
        c = Course.objects.create(name='Только 7F', is_visible=True, visible_classes='7F', created_by=self.teacher)
        Course.objects.create(name='Для всех', is_visible=True)
        self.client.login(username='s7', password='p')
        r = self.client.get(reverse('courses'))
        self.assertContains(r, 'Только 7F')
        self.assertContains(r, 'Для всех')
        self.assertContains(r, 'Нур Омарбек')
        self.client.logout()
        self.client.login(username='s9', password='p')
        r = self.client.get(reverse('courses'))
        self.assertNotContains(r, 'Только 7F')
        self.assertContains(r, 'Для всех')
        r = self.client.get(reverse('course_detail', args=[c.id]))
        self.assertRedirects(r, reverse('courses'))

    def test_edit_updates_classes(self):
        c = Course.objects.create(name='X', is_visible=True, visible_classes='7F')
        self.teacher.courses.add(c)
        self.client.login(username='tc', password='p')
        self.client.post(reverse('course_edit', args=[c.id]), {'name': 'X', 'visible_classes': ['8A']})
        c.refresh_from_db()
        self.assertEqual(c.visible_class_list(), ['8A'])


class SiteIconTests(TestCase):
    def test_manifest_and_head_links(self):
        r = self.client.get('/manifest.webmanifest')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['short_name'], 'Pifagor')
        self.assertTrue(any(i['sizes'] == '512x512' for i in data['icons']))
        h = self.client.get('/login/').content.decode()
        for needle in ('rel="icon"', 'apple-touch-icon', 'rel="manifest"'):
            self.assertIn(needle, h)

    def test_favicon_redirect(self):
        self.assertEqual(self.client.get('/favicon.ico').status_code, 302)


class NavbarLogoTests(TestCase):
    def test_logo_in_navbar(self):
        u = User.objects.create_user('nl', password='pw12345')
        Student.objects.create(user=u, full_name='Н', school_class='7F')
        self.client.login(username='nl', password='pw12345')
        from unittest import mock
        import datetime
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 10, 10)):
            self.assertIn('icons/logo-tree', self.client.get('/').content.decode())


class RandomSlotPoolTests(RetakeTestBase):
    """Учитель выбирает, какие именно задачи могут выпасть по условию «тег + сложность»."""

    def post(self, pools, exam=None):
        self.client.force_login(self.teacher_user)
        start = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        n = len(pools)
        data = {'kind': 'SOR', 'title': 'Пул', 'school_class': '7F', 'start_at': start,
                'duration_minutes': '40', 'mode': 'random',
                'slot_tag': [str(self.tag1.id)] * n, 'slot_level': ['A'] * n, 'slot_pool': pools}
        url = reverse('exam_create') if exam is None else reverse('exam_edit', args=[exam.id])
        return self.client.post(url, data)

    def test_pool_saved_and_limits_candidates(self):
        keep = [self.a_tasks[0].id, self.a_tasks[1].id]
        self.post([','.join(map(str, keep))])
        exam = Exam.objects.get(title='Пул')
        self.assertEqual(exam.random_slots[0]['tasks'], sorted(keep))
        from .exam_views import _slot_candidates
        self.assertEqual(set(_slot_candidates(exam.random_slots[0])), set(keep))
        self.assertIn('выбрано задач: 2', exam.slot_list()[0]['label'])

    def test_students_get_only_pool_tasks(self):
        keep = [self.a_tasks[2].id, self.a_tasks[3].id]
        self.post([','.join(map(str, keep))])
        exam = Exam.objects.get(title='Пул')
        exam.start_at = timezone.now() - timedelta(minutes=5); exam.save()
        for st in self.students:
            self.client.force_login(st.user)
            self.client.post(reverse('exam_start', args=[exam.id]))
            att = ExamAttempt.objects.get(exam=exam, student=st)
            self.assertTrue(set(att.task_ids) <= set(keep))

    def test_all_selected_means_no_restriction(self):
        everything = ','.join(str(t.id) for t in self.a_tasks)
        self.post([everything])
        self.assertNotIn('tasks', Exam.objects.get(title='Пул').random_slots[0])

    def test_foreign_tasks_ignored_and_empty_rejected(self):
        self.post([str(self.b_tasks[0].id)])   # задача другого тега/сложности
        self.assertFalse(Exam.objects.filter(title='Пул').exists())

    def test_not_enough_tasks_in_pool_rejected(self):
        one = str(self.a_tasks[0].id)
        self.post([one, one])   # два условия, а в наборе одна задача
        self.assertFalse(Exam.objects.filter(title='Пул').exists())

    def test_edit_form_keeps_pool(self):
        keep = [self.a_tasks[0].id, self.a_tasks[1].id]
        self.post([','.join(map(str, keep))])
        exam = Exam.objects.get(title='Пул')
        html = self.client.get(reverse('exam_edit', args=[exam.id])).content.decode()
        self.assertIn(f'name="slot_pool" value="{keep[0]},{keep[1]}"', html)


class DuelsClassAccessTests(TestCase):
    def setUp(self):
        from .models import SiteSettings
        self.tu = User.objects.create_user('tcls', password='x', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Учитель', classes='7F')
        self.s7 = Student.objects.create(user=User.objects.create_user('a7', password='x'), full_name='A', school_class='7F')
        self.s8 = Student.objects.create(user=User.objects.create_user('b8', password='x'), full_name='B', school_class='8A')
        self.settings = SiteSettings.get()

    def open_class(self, name, action='open'):
        self.client.login(username='tcls', password='x')
        return self.client.post(reverse('duels_class_toggle'), {'school_class': name, 'action': action})

    def test_teacher_opens_own_class(self):
        from . import duels
        self.assertFalse(duels.visible_to(self.s7))
        self.open_class('7F')
        self.assertTrue(duels.visible_to(self.s7))
        self.assertFalse(duels.visible_to(self.s8))
        self.client.login(username='a7', password='x')
        self.assertIn('/duels/', self.client.get(reverse('all_tasks')).content.decode())
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)

    def test_close_class(self):
        from . import duels
        self.open_class('7F'); self.open_class('7F', 'close')
        self.assertFalse(duels.visible_to(self.s7))

    def test_cannot_open_foreign_class(self):
        from . import duels
        self.open_class('8A')
        self.assertFalse(duels.visible_to(self.s8))

    def test_global_off_overrides_classes(self):
        from . import duels
        self.open_class('7F')
        self.settings.duels_mode = 'off'; self.settings.save()
        self.assertFalse(duels.visible_to(self.s7))

    def test_overview_shows_class_buttons(self):
        self.open_class('7F')
        html = self.client.get(reverse('duels')).content.decode()
        self.assertIn('Доступ по классам', html)
        self.assertIn('✓ 7F', html)
        self.assertNotIn('name="school_class" value="8A"', html)


class DuelClassTagsTests(TestCase):
    def setUp(self):
        self.tu = User.objects.create_user('tg', password='x', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Т', classes='7F')
        self.a = Student.objects.create(user=User.objects.create_user('ga', password='x'), full_name='A', school_class='7F')
        self.b = Student.objects.create(user=User.objects.create_user('gb', password='x'), full_name='B', school_class='7F')
        Task.objects.all().delete()
        self.t_loop, _ = Tag.objects.get_or_create(name='циклы-тест')
        self.t_str, _ = Tag.objects.get_or_create(name='строки-тест')
        tc = [{'input': '1', 'output': '1'}]
        self.loops = []
        self.strs = []
        for i in range(4):
            t = Task.objects.create(title=f'L{i}', description='d', level='A', test_cases=tc); t.tags.add(self.t_loop); self.loops.append(t)
            t = Task.objects.create(title=f'S{i}', description='d', level='A', test_cases=tc); t.tags.add(self.t_str); self.strs.append(t)

    def set_tags(self, tag_ids, cls='7F'):
        """Темы теперь задаются параллели (у 7F — параллель «7»)."""
        from .models import SiteSettings
        ss = SiteSettings.get(); ss.duels_group_tags = {'7': list(tag_ids)} if tag_ids else {}; ss.save()

    def test_duel_tasks_only_from_chosen_tags(self):
        from . import duels
        self.set_tags([self.t_loop.id])
        for _ in range(10):
            picked = duels.pick_tasks(self.a, self.b)
            self.assertEqual({t.id for t in picked} - {t.id for t in self.loops}, set())
            self.assertEqual(len(picked), 3)

    def test_no_tags_means_any(self):
        from . import duels
        self.set_tags([self.t_loop.id]); self.set_tags([])
        seen = set()
        for _ in range(40):
            seen |= {t.id for t in duels.pick_tasks(self.a, self.b)}
        self.assertTrue(seen & {t.id for t in self.loops} and seen & {t.id for t in self.strs})

    def test_grade_groups_topics_and_matching(self):
        from . import duels
        from .models import DuelQueueEntry, Tag
        io, cond, loop = (Tag.objects.create(name=n) for n in ('input/output', 'if/else', 'for'))
        tc = self.loops[0].test_cases
        mine = []
        for i, tg in enumerate((io, cond, loop)):
            for j in range(2):
                t = Task.objects.create(title=f'G{i}{j}', description='d', level='A', test_cases=tc); t.tags.add(tg); mine.append(t.id)
        from .models import SiteSettings
        u_adm = User.objects.create_superuser('dadm', password='x')
        self.client.force_login(u_adm)
        self.client.post(reverse('duels_group_tags'), {'group': '7', 'tags': [io.id, cond.id, loop.id]})
        self.assertEqual(SiteSettings.get().duels_group_tags, {'7': sorted([io.id, cond.id, loop.id])})
        for _ in range(10):                                   # 7 классы: только input/output, if/else, for
            self.assertTrue({t.id for t in duels.pick_tasks(self.a, self.b)} <= set(mine))
        self.client.login(username='tg', password='x')         # обычный учитель темы параллели менять не может
        self.client.post(reverse('duels_group_tags'), {'group': '7', 'tags': []})
        self.assertIn('7', SiteSettings.get().duels_group_tags)
        self.assertEqual((duels.grade_group(self.a), duels.grade_group(Student(school_class='6B')),
                          duels.grade_group(Student(school_class='9A')), duels.grade_group(Student(school_class='11C'))),
                         ('7', '5-6', '8-9', '10-11'))
        u = User.objects.create_user('g9', password='x')
        nine = Student.objects.create(user=u, full_name='Девятый', school_class='9A')
        DuelQueueEntry.objects.create(student=self.a, rating=800)
        DuelQueueEntry.objects.create(student=nine, rating=800)
        self.assertIsNone(duels.try_match(self.a))            # 7 класс не встречается с 9-м
        DuelQueueEntry.objects.create(student=self.b, rating=800)
        self.assertIsNotNone(duels.try_match(self.a))         # 7F — 7-й параллели: встречаются

    def test_student_picks_older_grade_topics_of_older(self):
        from . import duels
        from .models import DuelQueueEntry, DuelTask, SiteSettings, Tag
        io, st = Tag.objects.create(name='input/output'), Tag.objects.create(name='string')
        tc = self.loops[0].test_cases
        seven, eight = [], []
        for j in range(3):
            t = Task.objects.create(title=f'IO{j}', description='d', level='A', test_cases=tc); t.tags.add(io); seven.append(t.id)
            t = Task.objects.create(title=f'ST{j}', description='d', level='A', test_cases=tc); t.tags.add(st); eight.append(t.id)
        ss = SiteSettings.get(); ss.duels_group_tags = {'7': [io.id], '8-9': [st.id]}; ss.save()
        self.assertEqual(duels.target_grades(self.a), ['7+', '8', '9', '10', '11'])
        e8 = Student.objects.create(user=User.objects.create_user('e8', password='x'), full_name='Восьмой', school_class='8A')
        self.assertNotIn('7', duels.target_grades(e8))                     # младших выбирать нельзя
        duels.join_queue(e8)                                               # 8A ждёт как обычно
        duels.join_queue(self.a)                                           # 7F — своя параллель: не встретятся
        self.assertIsNone(duels.try_match(self.a))
        duels.join_queue(self.a, '8')                                      # 7F выбрал 8 класс — встретились
        duel = duels.try_match(self.a)
        self.assertIsNotNone(duel)
        self.assertEqual(set(DuelTask.objects.filter(duel=duel).values_list('task_id', flat=True)), set(eight))  # темы старшего
        self.assertFalse(DuelQueueEntry.objects.exists())
        # «7 класс и старше»: подходит и свой 7-й, и 8-й
        e8b = Student.objects.create(user=User.objects.create_user('e8b', password='x'), full_name='Ещё восьмой', school_class='8B')
        duels.join_queue(e8b)
        duels.join_queue(self.b, '7+')
        self.assertIsNotNone(duels.try_match(self.b))

    def test_stale_duels_closed_on_page_view(self):
        from datetime import timedelta
        from django.core.cache import cache
        from . import duels
        from .models import Duel
        duels.join_queue(self.a); duels.join_queue(self.b)
        duel = duels.try_match(self.a)
        Duel.objects.filter(pk=duel.pk).update(created_at=timezone.now() - timedelta(minutes=30),
                                               last_activity=timezone.now() - timedelta(minutes=30))
        duel.duel_tasks.update(opened_at=None)
        cache.delete('duels:sweep')
        self.client.login(username='tg', password='x')
        self.client.get(reverse('duels'))
        self.assertEqual(Duel.objects.get(pk=duel.pk).status, 'FINISHED')

    def test_too_few_tagged_tasks_falls_back(self):
        from . import duels
        for t in self.loops[1:]:
            t.tags.clear()          # осталась одна задача с тегом «циклы»
        self.set_tags([self.t_loop.id])
        self.assertEqual(len(duels.pick_tasks(self.a, self.b)), 3)

    def test_overview_lists_group_tags(self):
        self.set_tags([self.t_loop.id])
        self.client.login(username='tg', password='x')
        html = self.client.get(reverse('duels')).content.decode()
        self.assertIn('Параллели', html)
        self.assertIn('циклы-тест', html)
        self.assertNotIn('Теги задач:', html)               # теги по классам больше не настраиваются


class DuelNoRepeatTests(TestCase):
    def setUp(self):
        from . import duels
        self.duels = duels
        self.a = Student.objects.create(user=User.objects.create_user('na', password='x'), full_name='A', school_class='7F')
        self.b = Student.objects.create(user=User.objects.create_user('nb', password='x'), full_name='B', school_class='7F')
        Task.objects.all().delete()
        self.tasks = [Task.objects.create(title=f'N{i}', description='d', level='A',
                                          test_cases=[{'input': '1', 'output': '1'}]) for i in range(7)]

    def test_tasks_from_previous_duels_are_not_repeated_while_others_exist(self):
        from .models import Duel, DuelTask
        d = Duel.objects.create(player1=self.a, player2=self.b)
        for i, t in enumerate(self.tasks[:3]):
            DuelTask.objects.create(duel=d, task=t, order=i)
        old = {t.id for t in self.tasks[:3]}
        for _ in range(20):
            picked = {t.id for t in self.duels.pick_tasks(self.a, self.b)}
            self.assertFalse(picked & old)

    def test_repeats_allowed_only_when_not_enough_other_tasks(self):
        from .models import Duel, DuelTask
        d = Duel.objects.create(player1=self.a, player2=self.b)
        for i, t in enumerate(self.tasks[:5]):
            DuelTask.objects.create(duel=d, task=t, order=i)
        picked = self.duels.pick_tasks(self.a, self.b)
        self.assertEqual(len(picked), 3)
        new = {t.id for t in self.tasks[5:]}
        self.assertTrue(new <= {t.id for t in picked})   # обе свежие задачи обязательно взяты


class TierBadgeAndAvatarTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('tier', password='x')
        self.st = Student.objects.create(user=self.u, full_name='Тир', school_class='7F')
        Task.objects.all().delete()
        from .models import Submission
        for i in range(12):
            t = Task.objects.create(title=f'Q{i}', description='d', level='A', test_cases=[])
            Submission.objects.create(student=self.st, task=t, status='DONE', score=10, is_graded=True)

    def test_tier_for_count(self):
        from .badge_art import tier_for_count
        self.assertIsNone(tier_for_count(9))
        self.assertEqual(tier_for_count(10)['tier'], 'wood')
        self.assertEqual(tier_for_count(120)['tier'], 'silver')
        self.assertEqual(tier_for_count(5000)['tier'], 'legend')

    def test_profile_shows_wood_frame_and_material_badges(self):
        self.client.login(username='tier', password='x')
        html = self.client.get(reverse('profile')).content.decode()
        self.assertIn('pf-avatar pf-wood', html)
        self.assertIn('tier-badge tier-wood', html)       # новый значок «Intern»
        self.assertIn('INTERN', html)
        self.assertIn('JUNIOR', html)                     # закрытый «Junior» тоже нарисован (с замком)
        self.assertNotIn('tier-badge tier-bronze', html)

    def test_leaderboard_has_frames(self):
        self.client.login(username='tier', password='x')
        html = self.client.get(reverse('leaderboard')).content.decode()
        self.assertIn('pf-avatar pf-wood', html)

    def test_no_frame_below_10(self):
        from .models import Submission
        Submission.objects.filter(student=self.st).first().delete()
        Submission.objects.filter(student=self.st).first().delete()
        Submission.objects.filter(student=self.st).first().delete()
        self.client.login(username='tier', password='x')
        html = self.client.get(reverse('profile')).content.decode()
        self.assertIn('class="pf-avatar"', html)
        self.assertNotIn('pf-avatar pf-', html)


class IndependenceDayTests(TestCase):
    def setUp(self):
        u = User.objects.create_user('hol', password='x')
        Student.objects.create(user=u, full_name='Х', school_class='7F')
        self.client.login(username='hol', password='x')

    def test_shown_on_dec_16_in_both_languages(self):
        from unittest import mock
        import datetime
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 12, 16)):
            html = self.client.get(reverse('all_tasks')).content.decode()
            self.assertIn('День Независимости', html)
            self.assertIn('kzw-nav', html)                     # флаг вместо логотипа
            s = self.client.session; s['content_lang'] = 'kk'; s.save()
            self.assertIn('Тәуелсіздік күні', self.client.get(reverse('all_tasks')).content.decode())

    def test_hidden_on_ordinary_day(self):
        from unittest import mock
        import datetime
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 10, 10)):
            html = self.client.get(reverse('all_tasks')).content.decode()
        self.assertNotIn('class="kz-holiday"', html)
        self.assertIn('logo-tree.png', html)

    def test_republic_day(self):
        from unittest import mock
        import datetime
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 10, 25)):
            html = self.client.get(reverse('all_tasks')).content.decode()
            self.assertIn('День Республики Казахстан', html)
            self.assertIn('kzw-nav', html)
            s = self.client.session; s['content_lang'] = 'kk'; s.save()
            self.assertIn('Республика күні', self.client.get(reverse('all_tasks')).content.decode())

    @override_settings(HOLIDAY_FORCE='independence')
    def test_force_setting(self):
        self.assertIn('День Независимости', self.client.get(reverse('all_tasks')).content.decode())


class AvatarAndRepublicBannerTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('av', password='x')
        self.st = Student.objects.create(user=self.u, full_name='Айгерим С', school_class='7F')
        self.client.login(username='av', password='x')

    def test_choose_free_avatar_and_back_to_default(self):
        self.client.post(reverse('profile_avatar'), {'avatar': 'school_girl'})
        self.st.refresh_from_db(); self.assertEqual(self.st.avatar, 'school_girl')
        html = self.client.get(reverse('profile')).content.decode()
        self.assertIn('avatars/school-girl', html)
        self.client.post(reverse('profile_avatar'), {'avatar': 'hacker_unknown'})   # чужой ключ — не меняется
        self.st.refresh_from_db(); self.assertEqual(self.st.avatar, 'school_girl')
        self.client.post(reverse('profile_avatar'), {'avatar': ''})
        self.st.refresh_from_db(); self.assertEqual(self.st.avatar, '')

    def test_republic_tasks_exist_with_tests(self):
        from .banners import REPUBLIC_TAG
        tasks = Task.objects.filter(tags__name=REPUBLIC_TAG)
        self.assertGreaterEqual(tasks.count(), 5)
        self.assertTrue(all(t.test_cases and t.title_kk and t.description_kk for t in tasks))

    def test_reference_solutions_pass(self):
        from .autotest import run_autotests
        sols = {
            'Қазақ хандығы': 'print(int(input())-1465)',
            'Абылай хан': 'y=int(input());print("YES" if 1711<=y<=1781 else "NO")',
            'Үш би': 'print(int(input())+int(input())+int(input()))',
            '«Оян, қазақ!»': 'for _ in range(int(input())): print("Oyan, qazaq!")',
            'Республика күні': 'd=int(input());m=int(input());y=int(input());print("YES" if d==25 and m==10 and y>=1990 else "NO")',
            'Кенесары көтерілісі': 'a=int(input());b=int(input());print(max(0,min(b,1847)-max(a,1837)+1))',
            'Алаш Орда': 'n=int(input());print(sum(1 for _ in range(n) if 1917<=int(input())<=1920))',
            'Аңырақай шайқасы': 'n=int(input());a=[int(input()) for _ in range(n)];print(a.index(max(a))+1)',
        }
        for title, code in sols.items():
            res = run_autotests(Task.objects.get(title=title), code)
            self.assertEqual(res['passed'], res['total'], title)

    def test_banner_after_five_tasks(self):
        from .models import Submission
        from .banners import REPUBLIC_TAG
        tasks = list(Task.objects.filter(tags__name=REPUBLIC_TAG)[:5])
        for t in tasks[:4]:
            Submission.objects.create(student=self.st, task=t, status='DONE', score=10, is_graded=True)
        html = self.client.get(reverse('republic_day')).content.decode()
        self.assertIn('4 / 5', html)
        self.st.refresh_from_db(); self.assertEqual(self.st.name_banner, '')
        Submission.objects.create(student=self.st, task=tasks[4], status='DONE', score=10, is_graded=True)
        self.client.get(reverse('republic_day'))
        self.st.refresh_from_db()
        self.assertEqual(self.st.name_banner, 'kz_flag')
        self.assertIn('has-b b-kz_flag', self.client.get(reverse('leaderboard')).content.decode())
        self.client.post(reverse('profile_banner'), {'banner': ''})
        self.st.refresh_from_db(); self.assertEqual(self.st.name_banner, '')

    def test_cannot_set_unowned_banner(self):
        self.client.post(reverse('profile_banner'), {'banner': 'kz_flag'})
        self.st.refresh_from_db(); self.assertEqual(self.st.name_banner, '')

    def test_gift_button_in_holiday_strip(self):
        from unittest import mock
        import datetime
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 10, 25)):
            html = self.client.get(reverse('all_tasks')).content.decode()
        self.assertIn('Хочешь подарок?', html)
        self.assertIn(reverse('republic_day'), html)


class CoinsAndShopTests(TestCase):
    def setUp(self):
        from .models import Submission
        self.Submission = Submission
        self.u = User.objects.create_user('coin', password='x')
        self.st = Student.objects.create(user=self.u, full_name='Монета', school_class='7F')
        Task.objects.all().delete()
        self.client.login(username='coin', password='x')

    def solve(self, level, when):
        from .models import Attempt
        t = Task.objects.create(title=f'T{Task.objects.count()}', description='d', level=level, test_cases=[])
        s = self.Submission.objects.create(student=self.st, task=t, status='DONE', score=10, is_graded=True)
        Attempt.objects.create(submission=s, number=1, submitted_at=when, score=10)
        return t

    def test_level_coins_daily_bonus_and_idempotent(self):
        from . import coins
        import datetime
        d1 = timezone.make_aware(datetime.datetime(2026, 10, 20, 10))
        self.solve('A', d1); self.solve('B', d1 + timedelta(hours=1)); self.solve('C', d1 + timedelta(days=1))
        coins.sync(self.st); coins.sync(self.st)
        # 1+2+3 за задачи + 5 + 5 за два дня
        self.assertEqual(coins.balance(self.st), 16)

    def test_milestone_every_50(self):
        from . import coins
        now = timezone.now()
        for i in range(50):
            self.solve('A', now - timedelta(days=60) + timedelta(minutes=i))
        coins.sync(self.st)
        # 50 задач по 1 + один день +5 + за 50 задач +25
        self.assertEqual(coins.balance(self.st), 50 + 5 + 25)

    def test_buy_banner(self):
        from . import coins
        from .models import CoinTransaction
        CoinTransaction.objects.create(student=self.st, key='test', kind='task', amount=310, at=timezone.now())
        self.client.post(reverse('shop_buy'), {'banner': 'gold'})          # 2000 — не хватает
        self.st.refresh_from_db(); self.assertNotIn('gold', self.st.owned_banners)
        self.client.post(reverse('shop_buy'), {'banner': 'oyu'})           # 300
        self.st.refresh_from_db()
        self.assertIn('oyu', self.st.owned_banners)
        self.assertEqual(self.st.name_banner, 'oyu')
        self.assertEqual(coins.balance(self.st), 10)
        self.client.post(reverse('shop_buy'), {'banner': 'oyu'})           # повторно не списывает
        self.assertEqual(coins.balance(self.st), 10)
        self.client.post(reverse('shop_buy'), {'banner': 'kz_flag'})       # подарок купить нельзя
        self.st.refresh_from_db(); self.assertNotIn('kz_flag', self.st.owned_banners)

    def test_free_banners_and_shop_page(self):
        self.client.post(reverse('profile_banner'), {'banner': 'mint'})
        self.st.refresh_from_db(); self.assertEqual(self.st.name_banner, 'mint')
        html = self.client.get(reverse('shop')).content.decode()
        self.assertIn('b-gold', html)
        self.assertIn('av-01.webp', html)               # аватары в магазине (раньше были заглушки «Скоро»)
        self.assertIn('coin-chip', html)

    def test_exam_ten_gives_50(self):
        from . import coins
        teacher = Teacher.objects.create(user=User.objects.create_user('tt', password='x'), full_name='T', classes='7F')
        t = self.solve('A', timezone.now() - timedelta(days=3))
        exam = Exam.objects.create(kind='SOR', title='СОР', school_class='7F', start_at=timezone.now() - timedelta(hours=3),
                                   duration_minutes=40, created_by=teacher)
        exam.tasks.set([t])
        att = ExamAttempt.objects.create(exam=exam, student=self.st, started_at=timezone.now() - timedelta(hours=3),
                                         finished_at=timezone.now() - timedelta(hours=2))
        ExamAnswer.objects.create(attempt=att, task=t, best_score=10, submissions_count=1)
        coins.sync(self.st)
        self.assertEqual(coins.balance(self.st), 1 + 5 + 50)


class CustomBannerTests(TestCase):
    def setUp(self):
        from .models import CoinTransaction
        self.u = User.objects.create_user('cb', password='x')
        self.st = Student.objects.create(user=self.u, full_name='Свой Баннер', school_class='7F')
        CoinTransaction.objects.create(student=self.st, key='seed', kind='task', amount=5000, at=timezone.now())
        self.client.login(username='cb', password='x')

    def test_buy_and_customize(self):
        from . import coins
        self.assertRedirects(self.client.get(reverse('shop_custom')), reverse('shop'))   # ещё не куплен
        r = self.client.post(reverse('shop_buy'), {'banner': 'custom'})
        self.assertRedirects(r, reverse('shop_custom'))
        self.assertEqual(coins.balance(self.st), 0)
        self.client.post(reverse('shop_custom'), {'c1': '#ff0000', 'c2': '#00ff00', 'fg': '#000000', 'icon': '🚀', 'fx': 'pulse'})
        self.st.refresh_from_db()
        self.assertEqual(self.st.custom_banner, {'c1': '#ff0000', 'c2': '#00ff00', 'fg': '#000000', 'icon': '🚀', 'fx': 'pulse'})
        self.assertEqual(self.st.name_banner, 'custom')
        from .models import Submission
        t = Task.objects.create(title='CB', description='d', level='A', test_cases=[])
        Submission.objects.create(student=self.st, task=t, status='DONE', score=10, is_graded=True)
        html = self.client.get(reverse('leaderboard')).content.decode()
        self.assertIn('--cb1:#ff0000', html)
        self.assertIn('fx-pulse', html)

    def test_bad_values_are_cleaned(self):
        self.client.post(reverse('shop_buy'), {'banner': 'custom'})
        self.client.post(reverse('shop_custom'), {'c1': 'red;background:url(x)', 'c2': '#12', 'fg': '#fff', 'icon': '<script>', 'fx': 'evil'})
        self.st.refresh_from_db()
        from .banners import CUSTOM_DEFAULT
        self.assertEqual(self.st.custom_banner, CUSTOM_DEFAULT)


class QuartersHallOfFameTests(TestCase):
    def setUp(self):
        from .models import Attempt, Submission
        self.Attempt, self.Submission = Attempt, Submission
        Task.objects.all().delete()
        self.users = []
        self.st = []
        for i, cls in enumerate(['7A', '7A', '7B', '8A']):
            u = User.objects.create_user(f'q{i}', password='x'); self.users.append(u)
            self.st.append(Student.objects.create(user=u, full_name=f'Ученик {i}', school_class=cls))

    def solve(self, st, level, when, score=10):
        t = Task.objects.create(title=f'QT{Task.objects.count()}', description='d', level=level, test_cases=[])
        s = self.Submission.objects.create(student=st, task=t, status='DONE', score=score, is_graded=True)
        self.Attempt.objects.create(submission=s, number=1, submitted_at=when, score=score)
        return s

    def test_scores_by_quarter_window(self):
        from . import quarters
        q1, q2 = quarters.BY_N[1], quarters.BY_N[2]
        self.solve(self.st[0], 'B', q1['end'] - timedelta(days=3))           # 1 четверть: 20
        self.solve(self.st[0], 'A', q2['start'] + timedelta(days=1))         # каникулы → 2 четверть: 10
        r1 = quarters.scores([self.st[0]], q1['start'], q1['end'])[self.st[0].id]
        r2 = quarters.scores([self.st[0]], q2['start'], q2['end'])[self.st[0].id]
        self.assertEqual((r1['score'], r2['score']), (20, 10))

    def test_freeze_once_with_awards_and_hall_page(self):
        from unittest import mock
        from . import quarters, coins
        from .models import HallEntry, QuarterFreeze
        q1 = quarters.BY_N[1]
        self.solve(self.st[0], 'C', q1['end'] - timedelta(days=5))   # 30
        self.solve(self.st[1], 'A', q1['end'] - timedelta(days=5))   # 10
        self.solve(self.st[2], 'B', q1['end'] - timedelta(days=5))   # 20
        after = q1['end'] + timedelta(hours=1)
        with mock.patch('django.utils.timezone.now', return_value=after):
            quarters.freeze_due(); quarters.freeze_due()
        self.assertEqual(QuarterFreeze.objects.filter(quarter=1).count(), 1)
        top7 = list(HallEntry.objects.filter(quarter=1, kind='student', grade='7').order_by('place'))
        self.assertEqual([e.name for e in top7], ['Ученик 0', 'Ученик 2', 'Ученик 1'])
        self.assertEqual(coins.balance(self.st[0]), 200)
        self.assertEqual(coins.balance(self.st[2]), 150)
        self.assertEqual(coins.balance(self.st[1]), 100)
        self.st[0].refresh_from_db(); self.assertIn('champion', self.st[0].owned_banners)
        classes7 = list(HallEntry.objects.filter(quarter=1, kind='class', grade='7').order_by('place'))
        self.assertEqual([c.name for c in classes7], ['7A', '7B'])
        # зал славы показывает зафиксированное
        with mock.patch('django.utils.timezone.now', return_value=after):
            self.client.force_login(self.users[0])
            html = self.client.get(reverse('hall_of_fame') + '?q=1&g=7').content.decode()
        self.assertIn('Ученик 0', html)
        self.assertIn('зафиксированы', html)

    def test_leaderboard_resets_in_new_quarter(self):
        from unittest import mock
        from . import quarters
        q1, q2 = quarters.BY_N[1], quarters.BY_N[2]
        self.solve(self.st[0], 'C', q1['end'] - timedelta(days=5))
        with mock.patch('django.utils.timezone.now', return_value=q2['start'] + timedelta(days=8)):
            self.client.force_login(self.users[1])
            html = self.client.get(reverse('leaderboard')).content.decode()
        self.assertNotIn('Ученик 0', html.split('lb-head')[1].split('Рейтинг классов')[0])
        self.assertIn('2 четверть', html)
        self.assertIn('Зал славы', html)
        # профиль: решённые задачи не обнулились
        self.client.login(username='q0', password='x')
        self.assertIn('>1<', self.client.get(reverse('profile')).content.decode().replace(' ', '').replace('\n', ''))


class HallOwnParallelTests(TestCase):
    def test_student_sees_only_own_parallel(self):
        from . import quarters
        from .models import HallEntry, QuarterFreeze
        u = User.objects.create_user('hp', password='x')
        Student.objects.create(user=u, full_name='Ученик 7', school_class='7A')
        u8 = User.objects.create_user('hp8', password='x')
        Student.objects.create(user=u8, full_name='Ученик 8', school_class='8A')
        QuarterFreeze.objects.create(year=quarters.YEAR, quarter=1, frozen_at=timezone.now())
        HallEntry.objects.create(year=quarters.YEAR, quarter=1, kind='student', grade='7', place=1, name='Семиклассник')
        HallEntry.objects.create(year=quarters.YEAR, quarter=1, kind='student', grade='8', place=1, name='Восьмиклассник')
        self.client.force_login(u)
        html = self.client.get(reverse('hall_of_fame') + '?q=1&g=8').content.decode()
        self.assertIn('Семиклассник', html)
        self.assertNotIn('Восьмиклассник', html)
        self.assertNotIn('g=8', html)


class QuarterCacheTests(TestCase):
    def test_cached_until_new_grade(self):
        from unittest import mock
        from . import quarters
        from .models import Submission
        u = User.objects.create_user('qc', password='x')
        st = Student.objects.create(user=u, full_name='К', school_class='7A')
        t = Task.objects.create(title='QC', description='d', level='A', test_cases=[])
        quarters.invalidate()
        with mock.patch('contest.quarters._compute', wraps=quarters._compute) as comp:
            quarters.scores([st]); quarters.scores([st]); quarters.scores([st])
            self.assertEqual(comp.call_count, 1)                       # дальше — из кэша
            Submission.objects.create(student=st, task=t, status='DONE', score=10, is_graded=True)
            self.assertEqual(quarters.scores([st])[st.id]['score'], 10)  # новая оценка сбросила кэш
            self.assertEqual(comp.call_count, 2)


class QuarterExclusionTests(TestCase):
    def test_excluded_student_hidden_and_others_move_up(self):
        from unittest import mock
        from . import quarters
        from .models import Attempt, HallEntry, QuarterExclusion, Submission
        Task.objects.all().delete()
        t_user = User.objects.create_user('tx', password='x', is_staff=True)
        Teacher.objects.create(user=t_user, full_name='T', classes='7B, 7C')
        q1 = quarters.BY_N[1]
        studs = []
        for i, (n, c, k) in enumerate([('Iskander Ryskulbekov', '7B', 5), ('Честный Первый', '7B', 4), ('Честный Второй', '7C', 3)]):
            u = User.objects.create_user(f'x{i}', password='x')
            st = Student.objects.create(user=u, full_name=n, school_class=c); studs.append(st)
            for j in range(k):
                t = Task.objects.create(title=f'X{i}{j}', description='d', level='A', test_cases=[])
                s = Submission.objects.create(student=st, task=t, status='DONE', score=10, is_graded=True)
                Attempt.objects.create(submission=s, number=1, submitted_at=q1['end'] - timedelta(days=3), score=10)
        before = q1['end'] - timedelta(days=1)
        with mock.patch('django.utils.timezone.now', return_value=before):
            self.client.force_login(t_user)
            self.client.post(reverse('leaderboard_exclude', args=[studs[0].id]), {'reason': 'списывание'})
            html = self.client.get(reverse('leaderboard')).content.decode()
        self.assertTrue(QuarterExclusion.objects.filter(student=studs[0], quarter=1).exists())
        board = html.split('lb-head')[1].split('Убраны из рейтинга')[0]
        self.assertNotIn('Iskander', board)
        self.assertIn('Убраны из рейтинга', html)
        with mock.patch('django.utils.timezone.now', return_value=q1['end'] + timedelta(hours=1)):
            quarters.freeze_due()
        top = list(HallEntry.objects.filter(quarter=1, kind='student', grade='7').order_by('place').values_list('name', flat=True))
        self.assertEqual(top, ['Честный Первый', 'Честный Второй'])
        from . import coins
        self.assertEqual(coins.balance(studs[0]) - quarters.AWARD_COINS.get(0, 0), 0)   # без наград


class AwardForPlaceTests(TestCase):
    def test_award_table(self):
        from .quarters import award_for
        self.assertEqual([award_for(p) for p in range(1, 22)], [500, 400, 300] + [200] * 7 + [100] * 10 + [0])
        self.assertEqual([award_for(p, 2) for p in (1, 2, 3, 4, 11)], [500, 400, 300, 200, 100])
        # 1 четверть уже выдана по старой таблице — она не меняется (иначе пересчёт доначислил бы всем)
        self.assertEqual([award_for(p, 1) for p in range(1, 22)], [200, 150, 100] + [50] * 7 + [25] * 10 + [0])


class RankChipTests(TestCase):
    def test_rank_chip_levels(self):
        from contest.templatetags.badge_tags import rank_chip
        self.assertEqual(rank_chip(3), '')
        self.assertIn('Intern', rank_chip(10))
        self.assertIn('rk-bronze', rank_chip(60))
        self.assertIn('Junior', rank_chip(60))
        self.assertIn('Team Lead', rank_chip(700))
        self.assertIn('Architect', rank_chip(1000))


class TeacherParallelLeaderboardTests(TestCase):
    def test_teacher_sees_whole_parallel_and_own_classes(self):
        t_user = User.objects.create_user('tpar', password='x', is_staff=True)
        Teacher.objects.create(user=t_user, full_name='T', classes='7B, 8A')
        task = Task.objects.create(title='P', description='d', level='A', test_cases=[])
        from .models import Submission
        for i, (n, c) in enumerate([('Мой Ученик', '7B'), ('Чужой Ученик', '7K'), ('Восьмой Ученик', '8C')]):
            u = User.objects.create_user(f'p{i}', password='x')
            st = Student.objects.create(user=u, full_name=n, school_class=c)
            Submission.objects.create(student=st, task=task, status='DONE', score=10, is_graded=True)
        self.client.force_login(t_user)
        html = self.client.get(reverse('leaderboard')).content.decode()
        board = html.split('lb-head')[1]
        self.assertIn('Мой Ученик', board)
        self.assertIn('Чужой Ученик', board)          # вся 7 параллель, не только свои классы
        self.assertNotIn('Восьмой Ученик', board)
        self.assertEqual(board.count('leaderboard/exclude') if 'leaderboard/exclude' in board else board.count('lb-ex'), 1)  # 🚫 только у своего
        html8 = self.client.get(reverse('leaderboard') + '?grade=8').content.decode().split('lb-head')[1]
        self.assertIn('Восьмой Ученик', html8)
        mine = self.client.get(reverse('leaderboard') + '?scope=mine').content.decode().split('lb-head')[1]
        self.assertIn('Мой Ученик', mine)
        self.assertNotIn('Чужой Ученик', mine)


class RememberFiltersTests(TestCase):
    def test_student_list_keeps_class_filter_after_edit(self):
        t_user = User.objects.create_user('tflt', password='x', is_staff=True)
        Teacher.objects.create(user=t_user, full_name='T', classes='7B, 7C')
        self.client.force_login(t_user)
        self.client.get(reverse('student_list') + '?q=&class=7C')
        r = self.client.get(reverse('student_list'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('class=7C', r['Location'])
        self.client.get(reverse('student_list') + '?q=&class=')
        self.assertNotIn('class=7C', self.client.get(reverse('student_list'))['Location'])

    def test_all_tasks_restores_and_all_link_resets(self):
        u = User.objects.create_user('sflt', password='x')
        Student.objects.create(user=u, full_name='S', school_class='7B')
        self.client.force_login(u)
        self.client.get(reverse('all_tasks') + '?level=B')
        r = self.client.get(reverse('all_tasks'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('level=B', r['Location'])
        self.client.get(reverse('all_tasks') + '?all=1')
        self.assertNotIn('level=B', self.client.get(reverse('all_tasks'))['Location'])


class TeacherTop20CollapseTests(TestCase):
    def test_rows_after_20_are_collapsible(self):
        t_user = User.objects.create_user('t20', password='x', is_staff=True)
        Teacher.objects.create(user=t_user, full_name='T', classes='7B')
        task = Task.objects.create(title='P', description='d', level='A', test_cases=[])
        from .models import Submission
        for i in range(23):
            st = Student.objects.create(user=User.objects.create_user(f'c{i}', password='x'), full_name=f'U{i}', school_class='7B')
            Submission.objects.create(student=st, task=task, status='DONE', score=10, is_graded=True)
        self.client.force_login(t_user)
        html = self.client.get(reverse('leaderboard') + '?grade=7').content.decode()
        self.assertEqual(html.count(' lb-more"'), 3)
        self.assertIn('lb-more-btn', html)
        self.assertIn('(3)', html)


class DailyGiftTests(TestCase):
    def test_gift_once_per_day(self):
        from unittest import mock
        from . import coins
        u = User.objects.create_user('gft', password='x')
        st = Student.objects.create(user=u, full_name='G', school_class='7B')
        self.client.force_login(u)
        self.assertIn('id="gift-btn"', self.client.get(reverse('courses')).content.decode())
        r = self.client.post(reverse('daily_gift')).json()
        self.assertTrue(r['ok']); self.assertEqual(r['balance'], 5)
        r2 = self.client.post(reverse('daily_gift')).json()
        self.assertFalse(r2['ok']); self.assertEqual(coins.balance(st), 5)
        self.assertNotIn('id="gift-btn"', self.client.get(reverse('courses')).content.decode())
        with mock.patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=1)):
            self.client.force_login(u)
            self.assertTrue(self.client.post(reverse('daily_gift')).json()['ok'])
        self.assertEqual(coins.balance(st), 10)


class ClassBonusScaleTests(TestCase):
    def test_scale_minus10_to_plus10(self):
        from .models import ClassBonus
        t_user = User.objects.create_user('tbs', password='x', is_staff=True)
        Teacher.objects.create(user=t_user, full_name='T', classes='7B')
        Student.objects.create(user=User.objects.create_user('sbs', password='x'), full_name='S', school_class='7B')
        self.client.force_login(t_user)
        for p in (-10, 0, 10, 3, 11, -11):
            self.client.post(reverse('add_class_bonus'), {'school_class': '7B', 'points': p})
        self.assertEqual(sorted(ClassBonus.objects.values_list('points', flat=True)), [-10, 0, 3, 10])
        html = self.client.get(reverse('leaderboard') + '?scope=mine').content.decode()
        self.assertEqual(html.count('class="cb-pt'), 21)


class CoinGainAnimationTests(TestCase):
    def test_gain_shown_once_after_new_coins(self):
        u = User.objects.create_user('cga', password='x')
        st = Student.objects.create(user=u, full_name='C', school_class='7B')
        self.client.force_login(u)
        self.assertNotIn('pfCoinBurst(0', self.client.get(reverse('courses')).content.decode())
        task = Task.objects.create(title='G', description='d', level='C', test_cases=[])
        from .models import Submission
        Submission.objects.create(student=st, task=task, status='DONE', score=10, is_graded=True)
        s = self.client.session; s['coin_sync_at'] = 0; s.save()
        html = self.client.get(reverse('courses')).content.decode()
        self.assertIn('pfCoinBurst(8, null, 8)', html)        # 3 за задачу C + 5 первая задача дня
        self.assertNotIn('pfCoinBurst(8, null', self.client.get(reverse('courses')).content.decode())


class AutotestSandboxTests(TestCase):
    def test_student_code_does_not_see_server_secrets(self):
        import os
        from unittest import mock
        from . import autotest
        code = "import os\nprint(sorted(k for k in os.environ if 'SECRET' in k or 'POSTGRES' in k))"
        with mock.patch.dict(os.environ, {'DJANGO_SECRET_KEY': 'topsecret', 'POSTGRES_PASSWORD': 'pw'}):
            out, err = autotest._run_one(code, '')
        self.assertIsNone(err)
        self.assertEqual(out.strip(), '[]')

    def test_sandbox_command_and_exit_codes(self):
        from unittest import mock
        from . import autotest
        argv, wall, pre = autotest.build_command('/tmp/x.py', 'pifagorbox')
        self.assertEqual(argv[:5], ['sudo', '-n', '-u', 'pifagorbox', '--'])
        self.assertIn('/usr/bin/prlimit', argv); self.assertIn('/usr/bin/timeout', argv)
        self.assertEqual(argv[-2:], ['-I', '/tmp/x.py']); self.assertFalse(pre); self.assertGreater(wall, autotest.WALL_CLOCK_TIMEOUT)
        self.assertEqual(autotest.build_command('/tmp/x.py')[0][1], '/tmp/x.py')
        with mock.patch('contest.autotest._is_mac', return_value=True):        # macOS: без prlimit — свой лаунчер
            margv, _, mpre = autotest.build_command('/tmp/x.py', 'pifagorbox')
            self.assertEqual(margv[:5], ['sudo', '-n', '-u', 'pifagorbox', '--']); self.assertNotIn('/usr/bin/prlimit', margv)
            self.assertIn(autotest.MAC_LAUNCHER, margv); self.assertEqual(margv[-2:], ['-I', '/tmp/x.py']); self.assertFalse(mpre)
        import subprocess
        def fake(rc, err=''):
            return mock.patch('contest.autotest.subprocess.run', return_value=subprocess.CompletedProcess([], rc, '', err))
        with self.settings(AUTOTEST_SANDBOX_USER='pifagorbox'):
            with fake(137):
                self.assertEqual(autotest._run_one('print(1)', ''), ('', 'timeout'))
            with fake(152):
                self.assertEqual(autotest._run_one('print(1)', ''), ('', 'timeout'))
            with fake(1, 'sudo: a password is required'):
                self.assertIn('песочница', autotest._run_one('print(1)', '')[1])

    def test_sandbox_check_requires_user(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.settings(AUTOTEST_SANDBOX_USER=''):
            with self.assertRaises(CommandError):
                call_command('sandbox_check')


class SharedCacheSettingTests(TestCase):
    def test_cache_dir_env_switches_backend(self):
        import importlib, os, sys
        from unittest import mock
        with mock.patch.dict(os.environ, {'DJANGO_CACHE_DIR': '/tmp/pf-cache-test'}):
            mod = importlib.reload(importlib.import_module('pifagor_contest.settings'))
            self.assertIn('filebased', mod.CACHES['default']['BACKEND'])
        importlib.reload(importlib.import_module('pifagor_contest.settings'))


class RealIPMiddlewareTests(TestCase):
    def test_real_ip_only_when_trusted_and_from_loopback(self):
        from .models import LoginEvent
        u = User.objects.create_user('rip', password='pw12345')
        Student.objects.create(user=u, full_name='R', school_class='7B')
        kw = dict(REMOTE_ADDR='127.0.0.1', HTTP_X_REAL_IP='85.1.2.3')
        with self.settings(TRUST_X_REAL_IP=True):
            self.client.post(reverse('login'), {'username': 'rip', 'password': 'pw12345'}, **kw)
        self.assertEqual(LoginEvent.objects.filter(user=u).latest('id').ip, '85.1.2.3')
        self.client.logout()
        with self.settings(TRUST_X_REAL_IP=True):       # запрос не с сервера — заголовку не верим
            self.client.post(reverse('login'), {'username': 'rip', 'password': 'pw12345'}, REMOTE_ADDR='9.9.9.9', HTTP_X_REAL_IP='1.1.1.1')
        self.assertEqual(LoginEvent.objects.filter(user=u).latest('id').ip, '9.9.9.9')
        self.client.logout()
        with self.settings(TRUST_X_REAL_IP=False):      # выключено (Render) — не меняем
            self.client.post(reverse('login'), {'username': 'rip', 'password': 'pw12345'}, **kw)
        self.assertEqual(LoginEvent.objects.filter(user=u).latest('id').ip, '127.0.0.1')


class SubtopicTests(TestCase):
    def setUp(self):
        from .models import Subtopic, Topic
        self.Subtopic = Subtopic
        self.tu = User.objects.create_user('tsub', password='p', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.tu, full_name='Учитель')
        self.other_u = User.objects.create_user('tother', password='p', is_staff=True)
        self.other = Teacher.objects.create(user=self.other_u, full_name='Другой')
        self.course = Course.objects.create(name='Python', is_visible=True)
        self.teacher.courses.add(self.course)
        self.topic = Topic.objects.create(course=self.course, name="Циклы")
        self.su = User.objects.create_user('ssub', password='p')
        Student.objects.create(user=self.su, full_name='Ученик', school_class='7F')

    def test_hidden_when_no_subtopics(self):
        self.client.login(username='ssub', password='p')
        r = self.client.get(reverse('course_detail', args=[self.course.id]))
        self.assertNotContains(r, 'subtopic_theory')

    def test_shown_with_theory_button(self):
        sub = self.Subtopic.objects.create(topic=self.topic, name='Цикл for', theory_html='<p>hi</p>', theory_filename='a.html')
        self.Subtopic.objects.create(topic=self.topic, name='Цикл while')
        self.client.login(username='ssub', password='p')
        r = self.client.get(reverse('course_detail', args=[self.course.id]))
        self.assertContains(r, 'Цикл for')
        self.assertContains(r, reverse('subtopic_theory', args=[sub.id]))
        t = self.client.get(reverse('subtopic_theory', args=[sub.id]))
        self.assertContains(t, '<p>hi</p>')
        self.assertIn('sandbox', t['Content-Security-Policy'])

    def test_theory_404_without_file_and_for_hidden_course(self):
        empty = self.Subtopic.objects.create(topic=self.topic, name='Пусто')
        full = self.Subtopic.objects.create(topic=self.topic, name='Есть', theory_html='x', theory_filename='x.html')
        self.client.login(username='ssub', password='p')
        self.assertEqual(self.client.get(reverse('subtopic_theory', args=[empty.id])).status_code, 404)
        self.course.is_visible = False
        self.course.save()
        self.assertEqual(self.client.get(reverse('subtopic_theory', args=[full.id])).status_code, 404)

    def test_teacher_creates_edits_deletes(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.login(username='tsub', password='p')
        f = SimpleUploadedFile('t.html', '<b>Теория</b>'.encode('utf-8'))
        self.client.post(reverse('subtopic_create', args=[self.topic.id]), {'name': 'Новая', 'order': '2', 'theory_file': f})
        sub = self.Subtopic.objects.get(name='Новая')
        self.assertEqual(sub.theory_filename, 't.html')
        self.assertIn('Теория', sub.theory_html)
        page = self.client.get(reverse('topic_edit', args=[self.topic.id]))
        self.assertContains(page, 'Новая')
        self.client.post(reverse('subtopic_edit', args=[sub.id]), {'name': 'Переименована', 'order': '1', 'remove_theory': '1'})
        sub.refresh_from_db()
        self.assertEqual((sub.name, sub.theory_html), ('Переименована', ''))
        self.client.post(reverse('subtopic_delete', args=[sub.id]))
        self.assertFalse(self.Subtopic.objects.filter(id=sub.id).exists())

    def test_foreign_teacher_cannot_change(self):
        sub = self.Subtopic.objects.create(topic=self.topic, name='Чужая')
        self.client.login(username='tother', password='p')
        self.client.post(reverse('subtopic_delete', args=[sub.id]))
        self.client.post(reverse('subtopic_edit', args=[sub.id]), {'name': 'Взлом'})
        self.client.post(reverse('subtopic_create', args=[self.topic.id]), {'name': 'Левая'})
        sub.refresh_from_db()
        self.assertEqual(sub.name, 'Чужая')
        self.assertEqual(self.Subtopic.objects.count(), 1)

    def test_student_cannot_manage(self):
        self.client.login(username='ssub', password='p')
        r = self.client.post(reverse('subtopic_create', args=[self.topic.id]), {'name': 'X'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.Subtopic.objects.count(), 0)


class SubtopicTasksTests(TestCase):
    def setUp(self):
        from .models import Subtopic, Topic
        self.tu = User.objects.create_user('tst', password='p', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.tu, full_name='У')
        self.course = Course.objects.create(name='Py', is_visible=True)
        self.teacher.courses.add(self.course)
        self.topic = Topic.objects.create(course=self.course, name='Условный оператор')
        self.own = Task.objects.create(title='Своя задача темы', description='d', level='A')
        self.t1 = Task.objects.create(title='Задача подтемы 1', description='d', level='A')
        self.t2 = Task.objects.create(title='Задача подтемы 2', description='d', level='B')
        self.topic.tasks.add(self.own)
        self.sub = Subtopic.objects.create(topic=self.topic, name='Вложенные условия')
        self.sub.tasks.add(self.t1)
        self.su = User.objects.create_user('sst', password='p')
        Student.objects.create(user=self.su, full_name='С', school_class='7F')

    def test_topic_with_subtopics_hides_own_tasks_shows_subtask(self):
        self.client.login(username='sst', password='p')
        r = self.client.get(reverse('course_detail', args=[self.course.id]))
        self.assertNotContains(r, 'Своя задача темы')
        self.assertContains(r, 'Вложенные условия')
        self.assertContains(r, 'Задача подтемы 1')
        self.assertNotContains(r, 'Задача подтемы 2')
        self.assertContains(r, 'data-part="tasks"', count=1)   # у подтемы свой раскрывающийся список задач, у темы — нет

    def test_topic_without_subtopics_shows_own_tasks(self):
        self.sub.delete()
        self.client.login(username='sst', password='p')
        r = self.client.get(reverse('course_detail', args=[self.course.id]))
        self.assertContains(r, 'Своя задача темы')

    def test_teacher_sets_subtopic_tasks_and_topic_form_hides_checklist(self):
        self.client.login(username='tst', password='p')
        self.client.post(reverse('subtopic_edit', args=[self.sub.id]),
                         {'name': 'Вложенные условия', 'tasks': [self.t1.id, self.t2.id]})
        self.assertEqual(set(self.sub.tasks.values_list('id', flat=True)), {self.t1.id, self.t2.id})
        page = self.client.get(reverse('topic_edit', args=[self.topic.id]))
        self.assertNotContains(page, 'name="tasks"')
        # сохранение темы не стирает и не меняет задачи
        self.client.post(reverse('topic_edit', args=[self.topic.id]), {'name': 'Условный оператор', 'tasks': [self.t2.id]})
        self.assertEqual(list(self.topic.tasks.values_list('id', flat=True)), [self.own.id])

    def test_create_subtopic_with_tasks(self):
        self.client.login(username='tst', password='p')
        self.client.post(reverse('subtopic_create', args=[self.topic.id]), {'name': 'Новая', 'tasks': [self.t2.id]})
        from .models import Subtopic
        self.assertEqual(list(Subtopic.objects.get(name='Новая').tasks.values_list('id', flat=True)), [self.t2.id])

    def test_effective_task_ids_and_analytics(self):
        self.assertEqual(self.topic.effective_task_ids(), [self.t1.id])
        r = self.client.login(username='tst', password='p')
        page = self.client.get(reverse('course_analytics', args=[self.course.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'задач: 1')


class DeleteCourseTopicTests(TestCase):
    def setUp(self):
        from .models import Subtopic, Topic
        self.tu = User.objects.create_user('tdel', password='p', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.tu, full_name='Т')
        self.ou = User.objects.create_user('tdel2', password='p', is_staff=True)
        Teacher.objects.create(user=self.ou, full_name='Д')
        self.course = Course.objects.create(name='К', is_visible=True)
        self.teacher.courses.add(self.course)
        self.task = Task.objects.create(title='Z', description='d', level='A')
        self.topic = Topic.objects.create(course=self.course, name='Т1')
        self.sub = Subtopic.objects.create(topic=self.topic, name='П1')
        self.sub.tasks.add(self.task)
        self.Topic, self.Subtopic = Topic, Subtopic

    def test_delete_topic_removes_subtopics_keeps_task(self):
        self.client.login(username='tdel', password='p')
        self.client.post(reverse('topic_delete', args=[self.topic.id]))
        self.assertFalse(self.Topic.objects.filter(id=self.topic.id).exists())
        self.assertFalse(self.Subtopic.objects.filter(id=self.sub.id).exists())
        self.assertTrue(Task.objects.filter(id=self.task.id).exists())

    def test_delete_course_cascades(self):
        self.client.login(username='tdel', password='p')
        r = self.client.post(reverse('course_delete', args=[self.course.id]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Course.objects.filter(id=self.course.id).exists())
        self.assertEqual(self.Topic.objects.count(), 0)
        self.assertTrue(Task.objects.filter(id=self.task.id).exists())

    def test_get_does_not_delete_and_foreign_teacher_cannot(self):
        self.client.login(username='tdel', password='p')
        self.client.get(reverse('course_delete', args=[self.course.id]))
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())
        self.client.logout()
        self.client.login(username='tdel2', password='p')
        self.client.post(reverse('course_delete', args=[self.course.id]))
        self.client.post(reverse('topic_delete', args=[self.topic.id]))
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())
        self.assertTrue(self.Topic.objects.filter(id=self.topic.id).exists())

    def test_student_cannot_delete(self):
        u = User.objects.create_user('stdel', password='p')
        Student.objects.create(user=u, full_name='S', school_class='7F')
        self.client.login(username='stdel', password='p')
        self.client.post(reverse('course_delete', args=[self.course.id]))
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())


class CourseVisibilityInfoTests(TestCase):
    def test_manage_page_shows_who_sees_course(self):
        tu = User.objects.create_user('tvi', password='p', is_staff=True)
        teacher = Teacher.objects.create(user=tu, full_name='Т', classes='7F, 7K')
        for i, cls in enumerate(['7F', '7F', '7K']):
            Student.objects.create(user=User.objects.create_user(f'svi{i}', password='p'), full_name=f'С{i}', school_class=cls)
        c = Course.objects.create(name='Курс', is_visible=True, visible_classes='7F, 7K, 7B')
        teacher.courses.add(c)
        self.client.login(username='tvi', password='p')
        page = self.client.get(reverse('course_manage', args=[c.id])).content.decode()
        self.assertIn('Сейчас курс видят', page)
        self.assertIn('<strong>7F</strong> (2)', page)
        self.assertIn('7B', page.split('пока нет учеников')[0][-60:])
        c.is_visible = False
        c.save()
        self.assertIn('выключен', self.client.get(reverse('course_manage', args=[c.id])).content.decode())


class DisqualifyTests(TestCase):
    """Убрать 1–2 места параллели из итогов зафиксированной четверти: монеты назад, баннер снят, уведомление."""

    def setUp(self):
        from django.utils import timezone as tz
        from . import quarters
        from .models import CoinTransaction, HallEntry, QuarterFreeze
        self.q = quarters
        self.tu = User.objects.create_user('tdq', password='p', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.tu, full_name='Учитель', classes='7A, 7B')
        self.studs = []
        for i, (cls, banner) in enumerate([('7A', 'champion'), ('7B', ''), ('7A', ''), ('7B', '')], 1):
            u = User.objects.create_user(f'dq{i}', password='p')
            st = Student.objects.create(user=u, full_name=f'Ученик {i}', school_class=cls,
                                        owned_banners=banner, name_banner=banner)
            self.studs.append(st)
            HallEntry.objects.create(year=quarters.YEAR, quarter=1, kind='student', grade='7', place=i, student=st,
                                     name=st.full_name, school_class=cls, score=100 - i, solved=5)
            if i <= 3:
                CoinTransaction.objects.create(student=st, key=f'q:{quarters.YEAR}:1', kind='award',
                                               amount={1: 200, 2: 150, 3: 100}[i], at=tz.now(), note='награда')
        QuarterFreeze.objects.create(year=quarters.YEAR, quarter=1, frozen_at=tz.now())

    def test_disqualify_refunds_coins_removes_banner_notifies_and_rebuilds_hall(self):
        from .coins import balance
        from .disqualify import disqualify
        from .models import HallEntry, StudentNotice
        first, second, third = self.studs[:3]
        r1 = disqualify(first, 1, 'ИИ')
        r2 = disqualify(second, 1, 'ИИ')
        self.assertEqual((r1['refunded'], r1['banner_removed']), (200, True))
        self.assertEqual((r2['refunded'], r2['banner_removed']), (150, False))
        first.refresh_from_db()
        self.assertEqual((first.owned_banners, first.name_banner), ('', ''))
        self.assertEqual(balance(first), 0)
        self.assertEqual(balance(second), 0)
        self.assertEqual(balance(third), 100)            # другим ничего не меняется
        places = {e.student_id: e.place for e in HallEntry.objects.filter(quarter=1, kind='student')}
        self.assertNotIn(first.id, places)
        self.assertNotIn(second.id, places)
        self.assertIn(StudentNotice.objects.get(student=first).text.count('возвращены'), (1,))
        # повторный вызов ничего не возвращает второй раз
        self.assertEqual(disqualify(first, 1, 'ИИ')['refunded'], 0)
        self.assertEqual(balance(first), 0)

    def test_student_sees_notice_until_understood(self):
        from .disqualify import disqualify
        disqualify(self.studs[0], 1, 'ИИ')
        self.client.login(username='dq1', password='p')
        page = self.client.get(reverse('courses')).content.decode()
        self.assertIn('убрали из рейтинга 1 четверти', page)
        self.client.post(reverse('notice_seen'))
        self.assertNotIn('убрали из рейтинга 1 четверти', self.client.get(reverse('courses')).content.decode())

    def test_teacher_button_and_permissions(self):
        from .coins import balance
        self.client.login(username='tdq', password='p')
        page = self.client.get(reverse('hall_of_fame') + '?q=1&g=7').content.decode()
        self.assertIn('/hall-of-fame/disqualify/', page)
        r = self.client.post(reverse('hall_disqualify'), {'student_id': self.studs[0].id, 'quarter': 1, 'reason': 'ИИ'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(balance(self.studs[0]), 0)
        # ученик чужого класса — нельзя
        other = Student.objects.create(user=User.objects.create_user('oth', password='p'), full_name='Чужой', school_class='7Z')
        self.client.post(reverse('hall_disqualify'), {'student_id': other.id, 'quarter': 1})
        self.assertFalse(other.quarter_exclusions.exists())

    def test_student_cannot_disqualify(self):
        self.client.login(username='dq3', password='p')
        self.client.post(reverse('hall_disqualify'), {'student_id': self.studs[0].id, 'quarter': 1})
        self.assertFalse(self.studs[0].quarter_exclusions.exists())

    def test_management_command(self):
        from django.core.management import call_command
        from .coins import balance
        call_command('disqualify', quarter=1, grade='7', places='1,2', reason='ИИ', stdout=__import__('io').StringIO())
        self.assertEqual(balance(self.studs[0]), 0)
        self.assertEqual(balance(self.studs[1]), 0)
        self.assertEqual(balance(self.studs[2]), 100)


class DuelFullscreenTests(DuelTests):
    def test_active_duel_page_has_fullscreen_gate(self):
        duel = self.start()
        self.client.login(username='da', password='pw12345')
        html = self.client.get(reverse('duel_match', args=[duel.id])).content.decode()
        self.assertIn('id="fs-gate"', html)
        self.assertIn('requestFullscreen', html)
        self.assertIn('FS_GRACE', html)
        self.assertIn('Дуэль идёт в полноэкранном режиме', html)

    def test_finished_duel_page_exits_fullscreen_and_has_no_gate(self):
        duel = self.start()
        self.duels.resign(duel, self.st[0])
        self.client.login(username='da', password='pw12345')
        html = self.client.get(reverse('duel_match', args=[duel.id])).content.decode()
        self.assertNotIn('id="fs-gate"', html)
        self.assertIn('exitFullscreen', html)


class StudentActivityTests(TestCase):
    """Активность за период: считаются только впервые отправленные в периоде задачи, пересдачи — нет."""

    def setUp(self):
        from .models import Attempt, Submission
        self.now = timezone.now()
        self.tu = User.objects.create_user('act_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='7A')
        self.a = Student.objects.create(user=User.objects.create_user('act_a', password='p'), full_name='Аня', school_class='7A')
        self.b = Student.objects.create(user=User.objects.create_user('act_b', password='p'), full_name='Боря', school_class='7A')
        self.other = Student.objects.create(user=User.objects.create_user('act_o', password='p'), full_name='Чужой', school_class='9Z')
        Task.objects.all().delete()
        self.t = {lv: Task.objects.create(title=f'T{lv}{i}', description='d', level=lv, input_example='1',
                                          output_example='1', test_cases=[]) for i, lv in enumerate('ABC')}
        self.mk = lambda st, task, score, *hours_ago, graded=True: self._sub(st, task, score, hours_ago, graded)

    def _sub(self, st, task, score, hours_ago, graded=True):
        from .models import Attempt, Submission
        sub = Submission.objects.create(student=st, task=task, score=score, is_graded=graded, status='DONE' if graded else 'TESTING')
        for n, h in enumerate(sorted(hours_ago, reverse=True), 1):
            Attempt.objects.create(submission=sub, number=n, submitted_at=self.now - timedelta(hours=h), score=score if graded else None)
        Submission.objects.filter(pk=sub.pk).update(created_at=self.now - timedelta(hours=max(hours_ago)))
        return sub

    def rows(self, **params):
        self.client.login(username='act_t', password='p')
        r = self.client.get(reverse('student_activity'), params)
        self.assertEqual(r.status_code, 200)
        return {x['student'].full_name: x for x in r.context['rows']}, r

    def test_counts_new_tasks_only(self):
        self.mk(self.a, self.t['A'], 10, 0.5)              # первая отправка 30 мин назад — новая, решена
        self.mk(self.a, self.t['B'], 6, 0.4)               # новая, частично
        self.mk(self.a, self.t['C'], 0, 0.2, graded=False)  # новая, ждёт проверки
        rows, _ = self.rows(period='1h')
        self.assertEqual((rows['Аня']['solved'], rows['Аня']['partial'], rows['Аня']['pending'], rows['Аня']['new']), (1, 1, 1, 3))

    def test_resubmission_counts_only_if_score_grew(self):
        from .models import Attempt
        sub = self.mk(self.a, self.t['A'], 10, 30)         # раньше: 30 ч назад получил 10
        Attempt.objects.create(submission=sub, number=2, submitted_at=self.now - timedelta(hours=0.5), score=10)
        sub2 = self.mk(self.a, self.t['B'], 4, 30)         # раньше 4, теперь 4 — такой же
        Attempt.objects.create(submission=sub2, number=2, submitted_at=self.now - timedelta(hours=0.5), score=4)
        sub3 = self.mk(self.a, self.t['C'], 8, 30)         # раньше 8, теперь меньше (3)
        Attempt.objects.create(submission=sub3, number=2, submitted_at=self.now - timedelta(hours=0.5), score=3)
        self.mk(self.b, self.t['A'], 10, 0.5)              # новая
        rows, _ = self.rows(period='1d')
        self.assertEqual((rows['Аня']['solved'], rows['Аня']['new'], rows['Аня']['repeats']), (0, 0, 3))
        self.assertEqual(rows['Боря']['solved'], 1)
        rows, _ = self.rows(period='7d')                   # в окне 7 дней первая отправка уже внутри
        self.assertEqual(rows['Аня']['solved'], 1)

    def test_resubmission_with_higher_score_is_counted(self):
        from .models import Attempt
        sub = self.mk(self.a, self.t['A'], 4, 30)          # раньше 4
        Attempt.objects.create(submission=sub, number=2, submitted_at=self.now - timedelta(hours=0.5), score=10)
        sub2 = self.mk(self.a, self.t['B'], 2, 30)         # раньше 2, теперь 6 — выросло, но не 10
        Attempt.objects.create(submission=sub2, number=2, submitted_at=self.now - timedelta(hours=0.5), score=6)
        rows, _ = self.rows(period='1d')
        self.assertEqual((rows['Аня']['solved'], rows['Аня']['partial'], rows['Аня']['repeats']), (1, 1, 0))

    def test_period_boundaries_and_levels(self):
        self.mk(self.a, self.t['A'], 10, 3)                # 3 часа назад
        self.mk(self.a, self.t['B'], 10, 50)               # 50 часов назад
        rows, _ = self.rows(period='2h')
        self.assertEqual(rows['Аня']['new'], 0)
        rows, _ = self.rows(period='1d')
        self.assertEqual(rows['Аня']['solved'], 1)
        rows, _ = self.rows(period='3d')
        self.assertEqual(rows['Аня']['solved'], 2)
        rows, _ = self.rows(period='3d', level='B')
        self.assertEqual(rows['Аня']['solved'], 1)
        rows, _ = self.rows(period='3d', level=['A', 'C'])
        self.assertEqual(rows['Аня']['solved'], 1)

    def test_teacher_sees_only_own_classes_and_class_filter(self):
        rows, _ = self.rows()
        self.assertEqual(set(rows), {'Аня', 'Боря'})
        rows, _ = self.rows(**{'class': '7A'})
        self.assertEqual(set(rows), {'Аня', 'Боря'})
        rows, _ = self.rows(**{'class': '9Z'})
        self.assertEqual(set(rows), set())                 # чужой класс учителю недоступен

    def test_student_cannot_open_and_bad_period_falls_back(self):
        self.client.login(username='act_a', password='p')
        self.assertNotEqual(self.client.get(reverse('student_activity')).status_code, 200)
        rows, r = self.rows(period='zzz')
        self.assertEqual(r.context['period'], '1d')

    def test_task_list_shows_only_solved_and_partial(self):
        self.mk(self.a, self.t['A'], 10, 0.5)
        self.mk(self.a, self.t['B'], 6, 0.4)
        self.mk(self.a, self.t['C'], 0, 0.2, graded=False)
        rows, _ = self.rows(period='1h')
        self.assertEqual({t['kind'] for t in rows['Аня']['done_tasks']}, {'solved', 'partial'})
        self.assertEqual(rows['Аня']['pending'], 1)             # счётчик «на проверке» остаётся

    def test_tag_filter(self):
        from .models import Tag
        tg = Tag.objects.create(name='циклы')
        self.t['A'].tags.add(tg)
        self.mk(self.a, self.t['A'], 10, 0.5)
        self.mk(self.a, self.t['B'], 10, 0.5)
        rows, _ = self.rows(period='1h')
        self.assertEqual(rows['Аня']['solved'], 2)
        rows, _ = self.rows(period='1h', tag=tg.id)
        self.assertEqual(rows['Аня']['solved'], 1)


class ReconcileQuartersTests(TestCase):
    """После убирания 1–2 мест: убранным монеты назад, поднявшимся — награда за новое место, 1 место — баннер."""

    def setUp(self):
        from django.utils import timezone as tz
        from . import quarters
        from .models import CoinTransaction, HallEntry, QuarterFreeze, Submission
        self.q = quarters
        self.tu = User.objects.create_user('trc', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='7A')
        self.st = []
        for i in range(1, 5):
            u = User.objects.create_user(f'rc{i}', password='p')
            st = Student.objects.create(user=u, full_name=f'У{i}', school_class='7A',
                                        owned_banners='champion' if i == 1 else '', name_banner='champion' if i == 1 else '')
            self.st.append(st)
            HallEntry.objects.create(year=quarters.YEAR, quarter=1, kind='student', grade='7', place=i, student=st,
                                     name=st.full_name, school_class='7A', score=100 - i, solved=5)
            for n in range(5 - i):          # У1 решил 4 задачи, У2 — 3, … — реальные баллы в окне четверти
                task = Task.objects.create(title=f'rc{i}-{n}', description='d', level='A', input_example='1',
                                           output_example='1', test_cases=[])
                sub = Submission.objects.create(student=st, task=task, score=10, is_graded=True, status='DONE')
                Submission.objects.filter(pk=sub.pk).update(updated_at=quarters.BY_N[1]['end'] - timedelta(days=3))
            CoinTransaction.objects.create(student=st, key=f'q:{quarters.YEAR}:1', kind='award',
                                           amount=quarters.award_for(i, 1), at=tz.now(), note='награда')
        QuarterFreeze.objects.create(year=quarters.YEAR, quarter=1, frozen_at=tz.now())

    def _exclude_without_refund(self, *idx):
        from .models import QuarterExclusion
        for i in idx:                      # как будто убрали старым способом: исключение есть, остального нет
            QuarterExclusion.objects.create(student=self.st[i], year=self.q.YEAR, quarter=1, reason='ИИ')

    def test_reconcile_repairs_everything_and_is_idempotent(self):
        from .coins import balance
        from .disqualify import reconcile
        from .models import HallEntry
        self._exclude_without_refund(0, 1)
        r = reconcile(1)
        self.assertEqual({n for n, _ in r['refunded']}, {'У1', 'У2'})
        self.assertEqual(balance(self.st[0]), 0)                 # было 200, вернули
        self.assertEqual(balance(self.st[1]), 0)                 # было 150
        for s in self.st:
            s.refresh_from_db()
        # У3 поднялся на 1 место: 100 -> 200, У4 на 2: 50 -> 150
        self.assertEqual(balance(self.st[2]), 200)
        self.assertEqual(balance(self.st[3]), 150)
        self.assertIn('champion', self.st[2].owned_banners)      # новый чемпион
        self.assertNotIn('champion', self.st[0].owned_banners)   # у убранного снят
        self.assertEqual(self.st[0].name_banner, '')
        self.assertEqual(HallEntry.objects.get(kind='student', place=1).student_id, self.st[2].id)
        again = reconcile(1)
        self.assertEqual((again['refunded'], again['awarded'], again['champions']), ([], [], []))
        self.assertEqual(balance(self.st[2]), 200)

    def test_disqualify_promotes_next_students(self):
        from .coins import balance
        from .disqualify import disqualify
        disqualify(self.st[0], 1)
        disqualify(self.st[1], 1)
        for s in self.st:
            s.refresh_from_db()
        self.assertEqual((balance(self.st[0]), balance(self.st[1])), (0, 0))
        self.assertEqual((balance(self.st[2]), balance(self.st[3])), (200, 150))
        self.assertIn('champion', self.st[2].owned_banners)
        self.assertNotIn('champion', self.st[0].owned_banners)

    def test_button_view_and_permissions(self):
        self._exclude_without_refund(0)
        self.client.login(username='rc2', password='p')
        self.client.post(reverse('hall_reconcile'), {'quarter': 1})
        self.st[0].refresh_from_db(); self.assertIn('champion', self.st[0].owned_banners)   # ученику нельзя
        self.client.login(username='trc', password='p')
        r = self.client.post(reverse('hall_reconcile'), {'quarter': 1, 'grade': '7'}, follow=True)
        self.assertContains(r, 'Награды 1 четверти пересчитаны')
        self.st[0].refresh_from_db(); self.assertNotIn('champion', self.st[0].owned_banners)


class NamesAndEdupageTests(TestCase):
    """Сортировка по фамилии (А→Я, казахские буквы на своих местах) и страница «Имена из Edupage»."""

    def setUp(self):
        self.tu = User.objects.create_user('ed_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='7C')
        self.mk = lambda login, name, cls='7C': Student.objects.create(
            user=User.objects.create_user(login, password='p'), full_name=name, school_class=cls)

    def test_name_key_alphabet_and_surname_first(self):
        from .names import name_key
        names = ['Тайыр Айша', 'Әбілда Райымбек', 'Бейдальдин Аман', 'Ғабдығали Искандер', 'Гинаятова Айя', 'Абдыхан Жания']
        self.assertEqual(sorted(names, key=name_key),
                         ['Абдыхан Жания', 'Әбілда Райымбек', 'Бейдальдин Аман', 'Гинаятова Айя', 'Ғабдығали Искандер', 'Тайыр Айша'])

    def test_similarity_matches_latin_to_cyrillic(self):
        from .names import best_matches
        db = ['Adlet Aisha', 'Alexey Gorinov', 'Asanali', 'Eldar Alimhoja']
        edu = ['Алимқожа Эльдар', 'Горинов Алексей', 'Абултдинов Асанали', 'Адлет Аиша', 'Жеңіс Алиби']
        m = best_matches(db, edu)
        self.assertEqual([edu[m[i][0]] for i in range(4)],
                         ['Адлет Аиша', 'Горинов Алексей', 'Абултдинов Асанали', 'Алимқожа Эльдар'])

    def test_page_suggests_and_saves_only_roster_names(self):
        a = self.mk('e1', 'Adlet Aisha')
        b = self.mk('e2', 'Alexey Gorinov')
        self.client.login(username='ed_t', password='p')
        page = self.client.get(reverse('edupage_names') + '?class=7C')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'value="Адлет Аиша" selected')
        r = self.client.post(reverse('edupage_names'), {'class': '7C', f'name_{a.id}': 'Адлет Аиша', f'name_{b.id}': 'Кто-то левый'})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.full_name, 'Адлет Аиша')
        self.assertEqual(b.full_name, 'Alexey Gorinov')       # имени нет в списке Edupage — не принято

    def test_duplicate_choice_and_foreign_class_and_student_access(self):
        a = self.mk('e1', 'Adlet Aisha')
        b = self.mk('e2', 'Adlet Aisha2')
        other = self.mk('e3', 'Test Other', cls='7A')
        self.client.login(username='ed_t', password='p')
        self.client.post(reverse('edupage_names'), {'class': '7C', f'name_{a.id}': 'Адлет Аиша', f'name_{b.id}': 'Адлет Аиша'})
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(sorted([a.full_name, b.full_name]), sorted(['Адлет Аиша', 'Adlet Aisha2']))
        # чужой класс учителю недоступен: страница пустая, имена не меняются
        self.client.post(reverse('edupage_names'), {'class': '7A', f'name_{other.id}': 'Әбіл Ахмет'})
        other.refresh_from_db(); self.assertEqual(other.full_name, 'Test Other')
        self.client.login(username='e1', password='p')
        self.assertNotEqual(self.client.get(reverse('edupage_names')).status_code, 200)

    def test_student_list_sorted_by_surname(self):
        for i, n in enumerate(['Тайыр Айша', 'Әбілда Райымбек', 'Абдыхан Жания', 'Ғабдығали Искандер', 'Бейдальдин Аман']):
            self.mk(f's{i}', n)
        self.client.login(username='ed_t', password='p')
        r = self.client.get(reverse('student_list'), {'class': '7C'})
        self.assertEqual([s.full_name for s in r.context['students']],
                         ['Абдыхан Жания', 'Әбілда Райымбек', 'Бейдальдин Аман', 'Ғабдығали Искандер', 'Тайыр Айша'])

    def test_activity_sorted_by_surname(self):
        for i, n in enumerate(['Тайыр Айша', 'Абдыхан Жания', 'Бейдальдин Аман']):
            self.mk(f'a{i}', n)
        self.client.login(username='ed_t', password='p')
        r = self.client.get(reverse('student_activity'), {'class': '7C'})
        self.assertEqual([x['student'].full_name for x in r.context['rows']], ['Абдыхан Жания', 'Бейдальдин Аман', 'Тайыр Айша'])

    def test_auto_all_renames_only_confident_matches(self):
        a = self.mk('au1', 'Рамазан Исламов', cls='7C')        # имя-фамилия по-русски
        b = self.mk('au2', 'Alexey Gorinov')
        c = self.mk('au3', 'Совсем Другой')                      # похожего в Edupage нет
        self.client.login(username='ed_t', password='p')
        r = self.client.post(reverse('edupage_names'), {'action': 'auto_all'}, follow=True)
        self.assertEqual(r.status_code, 200)
        for s in (a, b, c):
            s.refresh_from_db()
        self.assertEqual(b.full_name, 'Горинов Алексей')
        self.assertEqual(c.full_name, 'Совсем Другой')
        self.assertContains(r, 'Совсем Другой')                  # в списке «проверить вручную»




class CodeGameTests(TestCase):
    """Игра «Бомба»: генерация с разными числами, вход 1 монета, приз, доступ только выбранным, выход из полного экрана."""

    def setUp(self):
        from .models import CodeGame, CoinTransaction
        self.tu = User.objects.create_user('cg_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='7A')
        self.st = Student.objects.create(user=User.objects.create_user('cg_s', password='p'), full_name='Аня', school_class='7A')
        self.other = Student.objects.create(user=User.objects.create_user('cg_o', password='p'), full_name='Боря', school_class='7B')
        self.foreign = Student.objects.create(user=User.objects.create_user('cg_f', password='p'), full_name='Чужой', school_class='9Z')
        self.game = CodeGame.objects.create(owner=self.tu, title='Бомба', mode='bomb', questions=4, time_limit=300, coins=2)
        self.game.set_classes(['7A']); self.game.save()
        CoinTransaction.objects.create(student=self.st, key='seed', kind='task', amount=5, at=timezone.now())

    def _start(self, stake=1):
        from .models import CodeGameRun
        self.client.login(username='cg_s', password='p')
        r = self.client.post(reverse('codegame_start', args=[self.game.id]), {'stake': stake})
        self.assertEqual(r.status_code, 302)
        run = CodeGameRun.objects.filter(student=self.st).latest('id')
        self.assertTrue(self.client.post(reverse('codegame_arm', args=[run.id])).json()['ok'])   # вошёл в полный экран
        return run

    def _ans(self, run, i, value):
        return self.client.post(reverse('codegame_answer', args=[run.id]), {'index': i, 'answer': value}).json()

    def _win(self, run):
        for i, q in enumerate(run.questions):
            if q['kind'] == 'wires':
                v = [k for k, o in enumerate(q['options']) if o.strip() == q['answer'].strip()][0]
            elif q['kind'] == 'pairs':
                v = ','.join(str([o.strip() for o in q['outputs']].index(a.strip())) for a in q['answers'])
            else:
                v = q['answer']
            d = self._ans(run, i, str(v))
            self.assertTrue(d['ok'])
        run.refresh_from_db()
        return run

    def test_generated_answers_are_real_outputs_and_topics(self):
        import random
        from . import codegame as cg
        self.game.topics = '@cond'
        qs = cg.generate(self.game, random.Random(1))
        self.assertEqual([q['kind'] for q in qs], ['keypad', 'wires', 'pairs', 'keypad'])
        bank = {t['code']: t['tags'] for t in cg.bank()}
        pairs = qs[2]
        self.assertEqual(len(pairs['codes']), 3)
        self.assertEqual(sorted(pairs['outputs']), sorted(pairs['answers']))
        self.assertEqual(len(set(a.strip() for a in pairs['answers'])), 3)       # выводы разные — пара однозначна
        for code, ans in zip(pairs['codes'], pairs['answers']):
            self.assertEqual(cg.run_output(code), ans)
        for q in [q for q in qs if q['kind'] != 'pairs']:
            self.assertEqual(cg.run_output(q['code']), q['answer'])
            self.assertNotIn('{{', q['code'])
            self.assertIn('@cond', bank[q['template']])
            if q['kind'] == 'wires':
                self.assertEqual([o.strip() for o in q['options']].count(q['answer'].strip()), 1)
        other = cg.generate(self.game, random.Random(2))
        self.assertNotEqual([q['code'] for q in qs], [q['code'] for q in other])

    def test_won_and_lost_pages_open(self):
        run = self._win(self._start())
        r = self.client.get(reverse('codegame_play', args=[run.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '+1')
        run2 = self._start()
        self.client.post(reverse('codegame_forfeit', args=[run2.id]))
        self.assertEqual(self.client.get(reverse('codegame_play', args=[run2.id])).status_code, 200)

    def test_free_entry_win_one(self):
        from .coins import balance
        run = self._start(stake=3)                              # ставки нет, вход бесплатный
        self.assertEqual(run.stake, 0)
        self.assertEqual(balance(self.st), 5)                   # ничего не списано
        run = self._win(run)
        self.assertEqual((run.status, run.coins), ('won', 1))
        self.assertEqual(balance(self.st), 6)                   # +1
        page = self.client.get(reverse('codegames')).content.decode()
        self.assertNotIn('−1', page)
        self.assertIn('+1', page)
        self.assertIn('Вход бесплатный', page)
        self.assertNotIn('name="stake"', page)
        self.assertNotIn('no-fs', page.split('</title>')[0])    # блок «нет полного экрана» не в заголовке страницы

    def test_pairs_wrong_match_is_a_strike(self):
        from .models import CodeGameRun
        run = self._start()
        i = [k for k, q in enumerate(run.questions) if q['kind'] == 'pairs'][0]
        q = run.questions[i]
        right = [[o.strip() for o in q['outputs']].index(a.strip()) for a in q['answers']]
        wrong = right[1:] + right[:1]
        d = self._ans(run, i, ','.join(map(str, wrong)))
        self.assertFalse(d['ok'])
        self.assertEqual(d['strikes'], 1)
        run.refresh_from_db()
        self.assertEqual(run.penalty, 60)
        self.assertTrue(self._ans(run, i, ','.join(map(str, right)))['ok'])

    def test_can_play_without_coins(self):
        from .models import CodeGameRun, CoinTransaction
        CoinTransaction.objects.filter(student=self.st).delete()
        self.client.login(username='cg_s', password='p')
        self.client.post(reverse('codegame_start', args=[self.game.id]))
        self.assertTrue(CodeGameRun.objects.filter(student=self.st, status='playing').exists())

    def test_game_started_with_paid_entry_still_pays_two(self):
        run = self._start()
        type(run).objects.filter(pk=run.pk).update(stake=1)      # начата до отмены платы за вход
        run = self._win(run)
        self.assertEqual(run.coins, 2)

    def test_each_mistake_costs_a_minute_no_strike_limit(self):
        from .coins import balance
        run = self._start()                                     # 5 минут
        for n in range(4):                                      # 4 ошибки = −4 минуты, игра продолжается
            d = self._ans(run, 0, 'неправильно')
            self.assertEqual(d['state'], 'playing')
        run.refresh_from_db()
        self.assertEqual((run.mistakes, run.penalty), (4, 240))
        self.assertLessEqual(d['left'], 60)
        d = self._ans(run, 0, 'неправильно')                    # пятая ошибка съела последнюю минуту — взрыв
        self.assertEqual(d['state'], 'lost')
        self.assertEqual(balance(self.st), 5)                   # проигрыш монет не отнимает

    def test_mistakes_then_win_still_pays(self):
        run = self._start()
        self._ans(run, 0, 'неправильно')
        self._ans(run, 0, 'неправильно')
        run = self._win(run)
        self.assertEqual((run.status, run.coins, run.penalty), ('won', 1, 120))

    def test_leaving_fullscreen_forfeits(self):
        run = self._start()
        r = self.client.post(reverse('codegame_forfeit', args=[run.id]))
        self.assertEqual(r.json()['state'], 'lost')
        self.assertEqual(self._ans(run, 0, 'x')['state'], 'lost')
        self.client.login(username='cg_o', password='p')       # чужое прохождение не трогается
        self.assertEqual(self.client.post(reverse('codegame_forfeit', args=[run.id])).status_code, 404)

    def test_answers_only_after_fullscreen_and_only_from_same_browser(self):
        from django.test import Client
        from .models import CodeGameRun
        self.client.login(username='cg_s', password='p')
        self.client.post(reverse('codegame_start', args=[self.game.id]))
        run = CodeGameRun.objects.filter(student=self.st).latest('id')
        d = self._ans(run, 0, run.questions[0]['answer'])          # без полного экрана ответ не принимается
        self.assertTrue(d.get('need_fullscreen'))
        self.assertFalse(d['ok'])
        self.client.post(reverse('codegame_arm', args=[run.id]))
        other = Client()                                            # тот же ученик в другом браузере
        other.login(username='cg_s', password='p')
        d = other.post(reverse('codegame_answer', args=[run.id]), {'index': 0, 'answer': run.questions[0]['answer']}).json()
        self.assertEqual(d['state'], 'lost')
        run.refresh_from_db()
        self.assertEqual(run.status, 'lost')

    def test_second_browser_cannot_arm_or_open(self):
        from django.test import Client
        run = self._start()
        other = Client()
        other.login(username='cg_s', password='p')
        self.assertFalse(other.post(reverse('codegame_arm', args=[run.id])).json()['ok'])
        run.refresh_from_db()
        self.assertEqual(run.status, 'lost')
        run2 = self._start()
        other.get(reverse('codegame_play', args=[run2.id]))         # просто открыл в другом браузере
        run2.refresh_from_db()
        self.assertEqual(run2.status, 'lost')

    def test_page_does_not_leak_answers_and_timeout_loses(self):
        from datetime import timedelta
        run = self._start()
        page = self.client.get(reverse('codegame_play', args=[run.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'fs-gate')
        self.assertTrue(all('answer' not in q and 'options' not in q for q in page.context['questions']))
        run.started_at = timezone.now() - timedelta(seconds=400)
        run.save()
        self.client.get(reverse('codegame_play', args=[run.id]))
        run.refresh_from_db()
        self.assertEqual(run.status, 'lost')

    def test_only_chosen_students_can_play_and_see_menu(self):
        self.client.login(username='cg_o', password='p')
        self.assertEqual(self.client.post(reverse('codegame_start', args=[self.game.id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('codegames')), '💣')
        self.client.login(username='cg_s', password='p')
        self.assertContains(self.client.get(reverse('codegames')), '💣')
        self.assertContains(self.client.get(reverse('profile')), '🎮')

    def test_teacher_creates_game_auto_title_bomb_only(self):
        from .models import CodeGame
        self.client.login(username='cg_t', password='p')
        r = self.client.post(reverse('codegames_manage'), {
            'mode': 'escape', 'topics': ['@cond'], 'questions': 6, 'minutes': 4, 'coins': 3,
            'is_active': 'on', 'classes': ['7A', '9Z']})
        g = CodeGame.objects.latest('id')
        self.assertRedirects(r, reverse('codegame_edit', args=[g.id]))
        self.assertEqual((g.mode, g.topics, g.time_limit), ('bomb', '@cond', 240))
        self.assertEqual(g.title, 'Бомба · 7A · if/else')
        self.assertEqual(g.class_list(), ['7A'])                       # чужой класс 9Z отфильтрован
        self.assertEqual(g.students.count(), 0)
        page = self.client.get(reverse('codegame_edit', args=[g.id]))
        self.assertNotContains(page, 'name="title"')
        self.assertNotContains(page, 'Escape')
        self.client.login(username='cg_s', password='p')
        self.assertNotEqual(self.client.get(reverse('codegames_manage')).status_code, 200)

    def test_access_by_class_not_by_student(self):
        from .models import CodeGame
        late = Student.objects.create(user=User.objects.create_user('cg_late', password='p'), full_name='Новенький', school_class='7A')
        self.client.login(username='cg_late', password='p')                # пришёл в класс позже — игра уже у него
        self.assertContains(self.client.get(reverse('codegames')), '💣')
        self.assertEqual(self.client.post(reverse('codegame_start', args=[self.game.id])).status_code, 302)
        self.game.is_active = False; self.game.save()
        self.assertEqual(self.client.post(reverse('codegame_start', args=[self.game.id])).status_code, 404)
        self.assertEqual(list(CodeGame.open_for(self.st)), [])
        legacy = CodeGame.objects.create(owner=self.tu, title='Старая', mode='bomb')
        legacy.students.add(self.other)                                   # личный доступ из старых данных работает
        self.assertEqual(list(CodeGame.open_for(self.other)), [legacy])

    def test_games_hub_for_teacher_only(self):
        self.client.login(username='cg_t', password='p')
        page = self.client.get(reverse('games_hub'))
        self.assertContains(page, 'Бомба')
        self.assertContains(page, reverse('codegames_manage'))
        self.assertContains(page, '7A')                                    # класс, которому открыта игра
        form = self.client.get(reverse('codegames_manage'))
        self.assertContains(form, 'name="classes"')
        self.assertNotContains(form, 'name="students"')
        self.client.login(username='cg_s', password='p')
        self.assertNotEqual(self.client.get(reverse('games_hub')).status_code, 200)

    def test_kazakh_ui(self):
        run = self._start()
        s = self.client.session; s['content_lang'] = 'kk'; s.save()
        page = self.client.get(reverse('codegame_play', args=[run.id])).content.decode()
        for kk in ('Бастау', 'Сапёр нұсқаулығы', 'Пернетақта', 'Сымдар', 'Бағдарлама шығаратыны'):
            self.assertIn(kk, page)
        self.assertNotIn('Инструкция сапёра', page)
        lst = self.client.get(reverse('codegames')).content.decode()
        self.assertIn('Ойнау', lst)
        self.assertIn('Енгізу-шығару және айнымалылар', lst)
        self.client.post(reverse('codegame_forfeit', args=[run.id]))
        self.assertIn('Бомба жарылды', self.client.get(reverse('codegame_play', args=[run.id])).content.decode())

    def test_coin_is_the_site_picture_not_emoji(self):
        run = self._start()
        for url in (reverse('codegames'), reverse('codegame_play', args=[run.id])):
            page = self.client.get(url).content.decode()
            self.assertIn('icons/coin-64.png', page)
            self.assertNotIn('🪙', page)


class AvatarShopTests(TestCase):
    """29 новых аватаров продаются в магазине за монеты; выбрать можно только бесплатные и купленные."""

    def setUp(self):
        from .models import CoinTransaction
        self.st = Student.objects.create(user=User.objects.create_user('av_s', password='p'), full_name='Аня', school_class='7A')
        CoinTransaction.objects.create(student=self.st, key='seed', kind='task', amount=120, at=timezone.now())
        self.client.login(username='av_s', password='p')

    def test_catalog_files_exist(self):
        import os
        from django.conf import settings
        from . import avatars
        paid = [a for a in avatars.CATALOG if a['kind'] == 'paid']
        self.assertEqual(len(paid), 33)
        self.assertEqual(avatars.BY_KEY['nurmash']['price'], 1000)
        base = os.path.join(settings.BASE_DIR, 'contest', 'static')
        for a in avatars.CATALOG:
            self.assertTrue(os.path.exists(os.path.join(base, a['file'])), a['file'])
            self.assertTrue(a['ru'] and a['kk'])

    def test_cannot_pick_unbought_avatar(self):
        self.client.post(reverse('profile_avatar'), {'avatar': 'av01'})
        self.st.refresh_from_db()
        self.assertEqual(self.st.avatar, '')

    def test_buy_avatar_spends_coins_and_puts_it_on(self):
        from .coins import balance
        self.client.post(reverse('shop_buy'), {'avatar': 'av01'})     # 100 монет
        self.st.refresh_from_db()
        self.assertEqual(self.st.avatar, 'av01')
        self.assertIn('av01', self.st.owned_avatars)
        self.assertEqual(balance(self.st), 20)
        self.client.post(reverse('shop_buy'), {'avatar': 'av01'})     # второй раз не списывает
        self.assertEqual(balance(self.st), 20)
        self.client.post(reverse('profile_avatar'), {'avatar': ''})
        self.client.post(reverse('profile_avatar'), {'avatar': 'av01'})  # купленный выбирается снова бесплатно
        self.st.refresh_from_db()
        self.assertEqual(self.st.avatar, 'av01')

    def test_not_enough_coins(self):
        from .coins import balance
        self.client.post(reverse('shop_buy'), {'avatar': 'av21'})     # дракон 500 > 120
        self.st.refresh_from_db()
        self.assertEqual((self.st.avatar, self.st.owned_avatars), ('', ''))
        self.assertEqual(balance(self.st), 120)
        page = self.client.get(reverse('shop')).content.decode()
        self.assertIn('av-21.webp', page)
        self.assertIn('не хватает 380', page)


class TeacherGrantCoinsTests(TestCase):
    """Учитель сам даёт монеты ученику своего класса: от 1 до 50 за раз, ученик получает уведомление."""

    def setUp(self):
        self.tu = User.objects.create_user('gr_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Нұр-Мұхаммед', classes='7A')
        self.st = Student.objects.create(user=User.objects.create_user('gr_s', password='p'), full_name='Аня', school_class='7A')
        self.foreign = Student.objects.create(user=User.objects.create_user('gr_f', password='p'), full_name='Чужой', school_class='9Z')
        self.client.login(username='gr_t', password='p')

    def test_grant_with_reason_and_notice(self):
        from .coins import balance
        from .models import StudentNotice
        r = self.client.post(reverse('student_coins', args=[self.st.id]), {'amount': 30, 'reason': 'помог однокласснику'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(balance(self.st), 30)
        n = StudentNotice.objects.get(student=self.st)
        self.assertIn('30', n.text)
        self.assertIn('помог однокласснику', n.text)
        self.client.post(reverse('student_coins', args=[self.st.id]), {'amount': 10})   # несколько раз — можно
        self.assertEqual(balance(self.st), 40)
        page = self.client.get(reverse('student_coins', args=[self.st.id]))
        self.assertContains(page, 'помог однокласснику — Нұр-Мұхаммед')

    def test_limits_and_permissions(self):
        from .coins import balance
        for bad in (0, 51, -5, 'abc'):
            self.client.post(reverse('student_coins', args=[self.st.id]), {'amount': bad})
        self.assertEqual(balance(self.st), 0)
        self.client.post(reverse('student_coins', args=[self.foreign.id]), {'amount': 10})
        self.assertEqual(balance(self.foreign), 0)                 # чужой класс
        self.client.login(username='gr_s', password='p')
        self.client.post(reverse('student_coins', args=[self.st.id]), {'amount': 50})
        self.assertEqual(balance(self.st), 0)                      # ученик сам себе не может

    def test_button_in_student_list(self):
        self.assertContains(self.client.get(reverse('student_list')), reverse('student_coins', args=[self.st.id]))


class NewBannersTests(TestCase):
    """14 новых платных баннеров есть в магазине и покупаются за монеты."""

    def test_new_banners_in_shop_and_buy(self):
        from .coins import balance
        from .models import CoinTransaction
        from . import banners
        st = Student.objects.create(user=User.objects.create_user('nb_s', password='p'), full_name='Аня', school_class='7A')
        CoinTransaction.objects.create(student=st, key='seed', kind='task', amount=500, at=timezone.now())
        self.client.login(username='nb_s', password='p')
        new = ['matrix', 'python', 'pixel', 'rainbow', 'ocean', 'sakura', 'ice', 'cyber', 'steppe', 'storm', 'shanyrak', 'rocket', 'chess', 'diamond']
        page = self.client.get(reverse('shop')).content.decode()
        for k in new:
            self.assertEqual(banners.CATALOG[k]['kind'], 'paid')
            self.assertIn(f'b-{k} sm', page)
            self.assertIn(f'.b-{k}', page)                       # стиль баннера подключён
        self.client.post(reverse('shop_buy'), {'banner': 'python'})
        st.refresh_from_db()
        self.assertIn('python', st.owned_banners)
        self.assertEqual(balance(st), 100)

    def test_row_banners_have_icon_and_no_grid_breaking_pseudo(self):
        from django.template.loader import render_to_string
        css = render_to_string('theme_head.html')
        self.assertIn('.has-b:is(', css)                          # значки ::before в строках выключены
        for k in ('chess', 'python', 'diamond', 'pixel', 'sakura', 'ice', 'storm'):
            self.assertIn(f'.b-{k}', css.split('.has-b:is(')[1].split(')::before')[0])
        from .templatetags.badge_tags import banner_icon
        self.assertIn('♞', banner_icon('chess'))


class CodeHintsTests(RetakeTestBase):
    def _live_exam(self):
        exam = Exam.objects.create(kind='SOR', title='СОР live', school_class='7F',
                                   start_at=timezone.now() - timedelta(minutes=5),
                                   duration_minutes=40, created_by=self.teacher)
        exam.tasks.set([self.a_tasks[0]])
        ExamAttempt.objects.create(exam=exam, student=self.students[0], started_at=timezone.now())
        self.client.force_login(self.students[0].user)
        return exam

    def test_exam_task_has_editor_with_hints(self):
        exam = self._live_exam()
        html = self.client.get(reverse('exam_task', args=[exam.id, self.a_tasks[0].id])).content.decode()
        self.assertIn('codemirror.min.js', html)
        self.assertIn('show-hint.min.js', html)
        self.assertIn('closebrackets.min.js', html)          # скобки закрываются сами
        self.assertIn('autoCloseBrackets: true', html)
        self.assertIn("'map'", html)
        self.assertIn('code-editor', html)

    def test_empty_code_does_not_spend_submission(self):
        exam = self._live_exam()
        self.client.post(reverse('exam_task', args=[exam.id, self.a_tasks[0].id]), {'code': '   '})
        self.assertFalse(ExamAnswer.objects.filter(task=self.a_tasks[0], attempt__exam=exam).exists())

    def test_run_button_with_pyodide_and_2s_limit(self):
        exam = self._live_exam()
        html = self.client.get(reverse('exam_task', args=[exam.id, self.a_tasks[0].id])).content.decode()
        self.assertIn('pyodide', html)
        self.assertIn('RUN_LIMIT_MS = 2000', html)
        self.assertIn('data-stdin=', html)
        self.assertIn('data-expect=', html)


class ExamFullscreenTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.live = Exam.objects.create(kind='SOR', title='СОР экран', school_class='7F',
                                        start_at=timezone.now() - timedelta(minutes=5),
                                        duration_minutes=40, created_by=self.teacher)
        self.live.tasks.set([self.a_tasks[0], self.b_tasks[0]])
        self.st = self.students[0]
        self.att = ExamAttempt.objects.create(exam=self.live, student=self.st, started_at=timezone.now())
        self.client.force_login(self.st.user)

    def arm(self, client=None):
        return (client or self.client).post(reverse('exam_arm', args=[self.att.id])).json()

    def test_start_page_explains_rules(self):
        self.att.delete()
        html = self.client.get(reverse('exam_detail', args=[self.live.id])).content.decode()
        self.assertIn('полноэкранном режиме', html)
        self.assertIn('exam-no-fs', html)

    def test_direct_open_goes_to_fullscreen_shell(self):
        r = self.client.get(reverse('exam_detail', args=[self.live.id]), headers={'Sec-Fetch-Dest': 'document'})
        self.assertRedirects(r, reverse('exam_run', args=[self.live.id]))
        r = self.client.get(reverse('exam_task', args=[self.live.id, self.a_tasks[0].id]), headers={'Sec-Fetch-Dest': 'document'})
        self.assertRedirects(r, reverse('exam_run', args=[self.live.id]))
        r = self.client.get(reverse('exam_task', args=[self.live.id, self.a_tasks[0].id]), headers={'Sec-Fetch-Dest': 'iframe'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'data-active="1"')
        self.assertNotEqual(r.headers.get('X-Frame-Options'), 'DENY')

    def test_tab_colors_follow_scores(self):
        from .models import ExamAnswer
        from . import exam_guard
        a, b = self.a_tasks[0], self.b_tasks[0]
        self.assertEqual(exam_guard.task_states(self.att), {})                         # ничего не отправлял — без цвета
        ExamAnswer.objects.create(attempt=self.att, task=a, best_score=10, submissions_count=1)
        ExamAnswer.objects.create(attempt=self.att, task=b, best_score=1, submissions_count=1)
        self.assertEqual(exam_guard.task_states(self.att), {a.id: 'ok', b.id: 'bad'})
        ExamAnswer.objects.filter(task=b).update(best_score=3)
        ExamAnswer.objects.filter(task=a).update(best_score=7)
        self.assertEqual(exam_guard.task_states(self.att), {a.id: 'part', b.id: 'bad'})     # 3 — ещё красная, жёлтая от 4
        ExamAnswer.objects.filter(task=b).update(best_score=4)
        self.assertEqual(exam_guard.task_states(self.att), {a.id: 'part', b.id: 'part'})
        ExamAnswer.objects.filter(task=b).update(submissions_count=0, best_score=0)      # запись есть, но не отправлено
        self.assertEqual(exam_guard.task_states(self.att), {a.id: 'part'})
        html = self.client.get(reverse('exam_run', args=[self.live.id])).content.decode()
        self.assertIn(f'data-task="{a.id}" class="part"', html)
        self.assertIn(f'data-task="{b.id}" class=""', html)
        self.assertEqual(self.client.get(reverse('exam_scores', args=[self.att.id])).json(), {'states': {str(a.id): 'part'}})

    def test_language_switch_inside_work(self):
        r = self.client.get(reverse('exam_run', args=[self.live.id]))
        self.assertContains(r, 'data-l="ru"')
        self.assertContains(r, 'data-l="kk"')
        import json
        tx = json.loads(r.context['i18n_json'])                              # тексты оболочки есть на обоих языках
        self.assertEqual(tx['ru']['back'], 'Вернуться в полноэкранный режим')
        self.assertNotEqual(tx['kk']['back'], tx['ru']['back'])
        self.assertEqual(set(tx['ru']), set(tx['kk']))
        url = reverse('exam_lang', args=[self.att.id])
        self.assertEqual(self.client.post(url, {'lang': 'kk'}).json(), {'ok': True, 'lang': 'kk'})
        self.assertEqual(self.client.session['content_lang'], 'kk')
        self.assertFalse(self.client.post(url, {'lang': 'zz'}).json()['ok'])
        self.assertEqual(self.client.session['content_lang'], 'kk')
        self.att.refresh_from_db()
        self.assertEqual(self.att.exits, 0)                                  # смена языка — не выход
        self.assertEqual(self.client.get(url).status_code, 405)
        other = Student.objects.exclude(id=self.st.id).first()
        self.client.force_login(other.user)
        self.assertEqual(self.client.post(url, {'lang': 'ru'}).status_code, 404)   # чужую попытку трогать нельзя

    def test_shell_has_task_tabs_and_no_exit_counting(self):
        r = self.client.get(reverse('exam_run', args=[self.live.id]))
        self.assertContains(r, 'Войти в полноэкранный режим')
        tabs = r.context['tabs']
        self.assertEqual([t['letter'] for t in tabs], ['A', 'B'])
        self.assertEqual(tabs[0]['url'], reverse('exam_task', args=[self.live.id, self.a_tasks[0].id]))
        self.assertNotContains(r, 'exam_leave')
        self.assertTrue(self.arm()['ok'])
        self.att.refresh_from_db()
        self.assertEqual(self.att.exits, 0)

    def test_reload_and_other_window_count_as_exits(self):
        self.client.get(reverse('exam_run', args=[self.live.id]))            # первый вход в оболочку — не выход
        self.att.refresh_from_db()
        self.assertEqual(self.att.exits, 0)
        self.arm()
        d = self.client.post(reverse('exam_exit', args=[self.att.id]), {'kind': 'blur'}).json()
        self.assertEqual((d['exits'], d['left']), (1, 1))
        r = self.client.get(reverse('exam_run', args=[self.live.id]))        # обновил страницу — второй выход, работа завершена
        self.assertEqual(r.status_code, 302)
        self.att.refresh_from_db()
        self.assertEqual((self.att.exits, self.att.finish_reason), (2, 'exits'))
        self.assertEqual([e['kind'] for e in self.att.exit_log], ['blur', 'reload'])

    def test_two_exits_finish_work_automatically(self):
        url = reverse('exam_exit', args=[self.att.id])
        self.arm()
        d = self.client.post(url).json()
        self.assertTrue(d['active'])
        self.assertEqual((d['exits'], d['left']), (1, 1))
        d = self.client.post(url).json()
        self.assertFalse(d['active'])
        self.att.refresh_from_db()
        self.assertEqual(self.att.exits, 2)
        self.assertEqual(self.att.finish_reason, 'exits')
        self.assertIsNotNone(self.att.finished_at)
        self.assertEqual(self.client.post(url).json()['exits'], 2)          # после конца уже не считается
        html = self.client.get(reverse('exam_detail', args=[self.live.id])).content.decode()
        self.assertIn('вышли из полноэкранного режима 2 раза', html)

    def test_exit_from_other_browser_not_counted_and_restore_resets(self):
        from django.test import Client
        self.arm()
        other = Client(); other.force_login(self.st.user)
        other.post(reverse('exam_exit', args=[self.att.id]))
        self.att.refresh_from_db()
        self.assertEqual(self.att.exits, 0)
        for _ in range(2):
            self.client.post(reverse('exam_exit', args=[self.att.id]))
        t = self.teacher.user if hasattr(self.teacher, 'user') else None
        self.client.force_login(self.teacher.user)
        self.client.post(reverse('exam_restore', args=[self.att.id]))
        self.att.refresh_from_db()
        self.assertEqual(self.att.exits, 0)
        self.assertIsNone(self.att.finished_at)

    def test_second_browser_is_locked(self):
        from django.test import Client
        self.arm()
        other = Client(); other.force_login(self.st.user)
        r = other.get(reverse('exam_task', args=[self.live.id, self.a_tasks[0].id]))
        self.assertContains(r, 'Работа открыта в другом браузере')
        other.get(reverse('exam_run', args=[self.live.id]))
        self.assertFalse(self.arm(other)['ok'])
        other.post(reverse('exam_task', args=[self.live.id, self.a_tasks[0].id]), {'code': 'print(1)'})
        self.assertFalse(ExamAnswer.objects.filter(attempt=self.att).exists())
        self.att.refresh_from_db()
        self.assertEqual(self.att.exit_log[-1]['kind'], 'device')

    def test_teacher_restores_access_on_other_computer(self):
        self.arm()
        self.client.force_login(self.teacher_user)
        self.assertContains(self.client.get(reverse('exam_results', args=[self.live.id])), 'Вернуть доступ')
        self.client.post(reverse('exam_restore', args=[self.att.id]))
        self.att.refresh_from_db()
        self.assertEqual(self.att.client, '')
        self.assertEqual(self.att.exit_log[-1]['kind'], 'restored')
        from django.test import Client
        other = Client(); other.force_login(self.st.user)
        self.assertTrue(self.arm(other)['ok'])

    def test_teacher_reopens_finished_work(self):
        self.att.finished_at = timezone.now(); self.att.save()
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('exam_restore', args=[self.att.id]))
        self.att.refresh_from_db()
        self.assertIsNone(self.att.finished_at)

    def test_restore_refused_after_time_is_over(self):
        self.att.finished_at = timezone.now(); self.att.save()
        self.live.start_at = timezone.now() - timedelta(hours=2); self.live.save()
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('exam_restore', args=[self.att.id]))
        self.att.refresh_from_db()
        self.assertIsNotNone(self.att.finished_at)

    def test_student_cannot_restore(self):
        self.att.finished_at = timezone.now(); self.att.save()
        self.client.post(reverse('exam_restore', args=[self.att.id]))
        self.att.refresh_from_db()
        self.assertIsNotNone(self.att.finished_at)

class OlympiadTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.secret = make_task('Олимп-задача', 'C', self.tag1)
        self.secret.hidden = True; self.secret.save()
        self.olymp = Exam.objects.create(kind='OLYM', title='Школьная олимпиада', school_class='',
                                         start_at=timezone.now() - timedelta(minutes=5), duration_minutes=120,
                                         created_by=self.teacher)
        self.olymp.tasks.set([self.secret, self.a_tasks[1]])
        self.in_student, self.out_student = self.students[0], self.students[1]
        self.olymp.participants.add(self.in_student)
        ou = User.objects.create_user('eighth', password='x')
        self.other_class_in = Student.objects.create(user=ou, full_name='Бекова Айя', school_class='8A')
        self.olymp.participants.add(self.other_class_in)

    def test_only_added_students_see_olympiad(self):
        self.client.force_login(self.in_student.user)
        page = self.client.get(reverse('olympiad_list'))
        self.assertContains(page, 'Школьная олимпиада')
        self.assertContains(page, reverse('olympiad_list'))                # пункт меню «Олимпиада»
        self.assertNotContains(self.client.get(reverse('exam_list')), 'Школьная олимпиада')   # не в СОР/СОЧ
        self.assertEqual(self.client.get(reverse('exam_detail', args=[self.olymp.id])).status_code, 200)
        self.client.force_login(self.other_class_in.user)              # другой класс — тоже видит
        self.assertContains(self.client.get(reverse('olympiad_list')), 'Школьная олимпиада')
        self.client.force_login(self.out_student.user)                  # одноклассник без приглашения — нет
        page = self.client.get(reverse('olympiad_list'))
        self.assertNotContains(page, 'Школьная олимпиада')
        self.assertNotContains(self.client.get(reverse('all_tasks')), reverse('olympiad_list'))   # и пункта меню нет
        r = self.client.get(reverse('exam_detail', args=[self.olymp.id]))
        self.assertRedirects(r, reverse('exam_list'))
        self.client.post(reverse('exam_start', args=[self.olymp.id]))
        self.assertFalse(ExamAttempt.objects.filter(exam=self.olymp, student=self.out_student).exists())

    def test_hidden_task_not_in_catalog_or_task_page(self):
        self.client.force_login(self.out_student.user)
        self.assertNotContains(self.client.get(reverse('all_tasks')), 'Олимп-задача')
        self.assertRedirects(self.client.get(reverse('task_detail', args=[self.secret.id])), reverse('all_tasks'))
        from .exam_views import _slot_candidates
        self.assertNotIn(self.secret.id, _slot_candidates({'tag': self.tag1.id, 'level': 'C'}))

    def test_participants_page_add_and_remove(self):
        self.client.force_login(self.teacher_user)
        url = reverse('olympiad_participants', args=[self.olymp.id])
        html = self.client.get(url).content.decode()
        self.assertIn('pick-class', html)
        self.assertIn('"8A"', html)                                       # классы для выпадающего списка
        self.client.post(url, {'action': 'add', 'student': self.out_student.id})
        self.assertTrue(self.olymp.participants.filter(id=self.out_student.id).exists())
        self.client.post(url, {'action': 'remove', 'student': self.out_student.id})
        self.assertFalse(self.olymp.participants.filter(id=self.out_student.id).exists())
        self.client.post(url, {'action': ['add', 'add_class'], 'school_class': '7F'})
        self.assertEqual(self.olymp.participants.filter(school_class='7F').count(), 3)

    def test_started_participant_cannot_be_removed(self):
        ExamAttempt.objects.create(exam=self.olymp, student=self.in_student, started_at=timezone.now())
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('olympiad_participants', args=[self.olymp.id]),
                         {'action': 'remove', 'student': self.in_student.id})
        self.assertTrue(self.olymp.participants.filter(id=self.in_student.id).exists())

    def test_create_olympiad_without_class_and_hide_tasks(self):
        self.client.force_login(self.teacher_user)
        start = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        r = self.client.post(reverse('exam_create'), {
            'kind': 'OLYM', 'title': 'Городская', 'start_at': start, 'duration_minutes': '180',
            'tasks': [self.b_tasks[2].id]})
        ex = Exam.objects.get(title='Городская')
        self.assertRedirects(r, reverse('olympiad_tasks', args=[ex.id]))
        self.assertEqual(ex.school_class, '')
        self.assertEqual(ex.tasks.count(), 0)          # задачи олимпиады — только из JSON, список в форме не используется

    def test_results_ranked_by_total(self):
        a1 = ExamAttempt.objects.create(exam=self.olymp, student=self.in_student, started_at=timezone.now())
        a2 = ExamAttempt.objects.create(exam=self.olymp, student=self.other_class_in, started_at=timezone.now())
        ExamAnswer.objects.create(attempt=a1, task=self.secret, best_score=4, submissions_count=1, submitted_at=timezone.now())
        ExamAnswer.objects.create(attempt=a2, task=self.secret, best_score=10, submissions_count=1, submitted_at=timezone.now())
        ExamAnswer.objects.create(attempt=a2, task=self.a_tasks[1], best_score=6, submissions_count=1, submitted_at=timezone.now())
        self.client.force_login(self.teacher_user)
        r = self.client.get(reverse('exam_results', args=[self.olymp.id]))
        rows = r.context['rows']
        self.assertEqual([row['student'] for row in rows], [self.other_class_in, self.in_student])
        self.assertEqual([row['total'] for row in rows], [16, 4])
        self.assertEqual(rows[0]['place'], 1)
        self.assertContains(r, '🥇')

    def test_olympiad_allows_more_submissions(self):
        from .exam_views import max_submissions, OLYMPIAD_MAX_SUBMISSIONS
        self.assertEqual(max_submissions(self.olymp), OLYMPIAD_MAX_SUBMISSIONS)
        self.assertEqual(max_submissions(self.exam), 3)

    def test_import_hidden_tasks(self):
        import json
        self.client.force_login(self.teacher_user)
        data = [{'title': 'Импорт-олимп', 'level': 'B', 'description': 'd', 'input_example': '1', 'output_example': '1',
                 'test_cases': [{'input': '1', 'output': '1'}]}]
        self.client.post(reverse('task_import'), {'json_text': json.dumps(data), 'hidden': '1'})
        self.assertTrue(Task.objects.get(title='Импорт-олимп').hidden)


class OlympiadUpsolveTests(RetakeTestBase):
    """Дорешивание олимпиады: после окончания (и всего доп. времени) — без ограничения времени, на места не влияет."""

    def setUp(self):
        super().setUp()
        self.secret = make_task('Олимп-задача', 'C', self.tag1)
        self.secret.hidden = True; self.secret.save()
        self.olymp = Exam.objects.create(kind='OLYM', title='Олимпиада Д', school_class='',
                                         start_at=timezone.now() - timedelta(hours=3), duration_minutes=60,
                                         created_by=self.teacher)
        self.olymp.tasks.set([self.secret])
        self.st = self.students[0]
        self.olymp.participants.add(self.st)
        self.attempt(self.olymp, self.st, 0)
        self.url = reverse('olympiad_upsolve_task', args=[self.olymp.id, self.secret.id])

    def test_upsolve_after_end_does_not_change_places(self):
        self.client.force_login(self.st.user)
        self.assertContains(self.client.get(reverse('olympiad_list')), reverse('olympiad_upsolve', args=[self.olymp.id]))
        self.assertContains(self.client.get(reverse('exam_detail', args=[self.olymp.id])), reverse('olympiad_upsolve', args=[self.olymp.id]))
        self.assertContains(self.client.get(reverse('olympiad_upsolve', args=[self.olymp.id])), 'Олимп-задача')
        self.client.post(self.url, {'code': 'print(1)', 'language': 'python'})
        from .models import UpsolveAnswer
        ans = UpsolveAnswer.objects.get(exam=self.olymp, student=self.st, task=self.secret)
        self.assertEqual(ans.best_score, 100)
        self.assertEqual(ExamAnswer.objects.get(attempt__exam=self.olymp, attempt__student=self.st).best_score, 0)
        self.client.force_login(self.teacher_user)
        html = self.client.get(reverse('exam_results', args=[self.olymp.id])).content.decode()
        self.assertIn('С дорешиванием', html)
        self.assertIn('Дорешивание: вкл', html)

    def test_closed_while_olympiad_or_extra_time_runs(self):
        self.client.force_login(self.st.user)
        other = self.students[1]
        self.olymp.participants.add(other)
        rt = self.retake(self.olymp, [other], start_delta=-5, minutes=60)      # у кого-то идёт доп. время
        self.assertRedirects(self.client.get(reverse('olympiad_upsolve', args=[self.olymp.id])), reverse('olympiad_list'))
        self.client.post(self.url, {'code': 'print(1)'})
        from .models import UpsolveAnswer
        self.assertFalse(UpsolveAnswer.objects.exists())
        rt.delete()
        self.olymp.start_at = timezone.now() - timedelta(minutes=5); self.olymp.save()     # олимпиада ещё идёт
        self.assertRedirects(self.client.get(reverse('olympiad_upsolve', args=[self.olymp.id])), reverse('olympiad_list'))

    def test_teacher_can_turn_off_and_non_participant_blocked(self):
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('olympiad_upsolve_toggle', args=[self.olymp.id]))
        self.olymp.refresh_from_db()
        self.assertFalse(self.olymp.upsolve)
        self.client.force_login(self.st.user)
        self.assertNotContains(self.client.get(reverse('olympiad_list')), reverse('olympiad_upsolve', args=[self.olymp.id]))
        self.assertRedirects(self.client.get(reverse('olympiad_upsolve', args=[self.olymp.id])), reverse('olympiad_list'))
        self.olymp.upsolve = True; self.olymp.save()
        self.client.force_login(self.students[1].user)                       # не участник
        self.assertRedirects(self.client.get(reverse('olympiad_upsolve', args=[self.olymp.id])), reverse('olympiad_list'))


class OlympiadGroupScoringTests(RetakeTestBase):
    """Олимпиадная задача: тесты по группам 20/50/30, задача — до 100 баллов."""
    def setUp(self):
        super().setUp()
        cases = [('aaabccdddd', 'a3bc2d4', 0), ('abc', 'abc', 0),
                 ('zzz', 'z3', 1), ('k', 'k', 1),
                 ('aabbb', 'a2b3', 2), ('abcdd', 'abcd2', 2),
                 ('a' * 12, 'a12', 3), ('b' * 10 + 'c', 'b10c', 3)]
        self.task = Task.objects.create(
            title='Олимп. Сжатие', level='C', description='d', input_example='abc', output_example='abc',
            hidden=True, test_groups=[20, 50, 30], time_limit=1,
            test_cases=[{'input': i, 'output': o, 'group': g} for i, o, g in cases])

    def test_group_points(self):
        from .autotest import group_summary, olympiad_points
        res = [{'ok': True, 'group': c['group']} for c in self.task.test_cases]
        self.assertEqual(olympiad_points(self.task, {'available': True, 'results': res, 'passed': 8, 'total': 8}), 100)
        res[-1]['ok'] = False                       # один тест 3-й группы не прошёл — вся группа 0
        groups, total, _ = group_summary(self.task, res)
        self.assertEqual(total, 70)
        self.assertEqual([g['got'] for g in groups], [20, 50, 0])
        res = [{'ok': c['group'] == 0, 'group': c['group']} for c in self.task.test_cases]   # только примеры
        self.assertEqual(group_summary(self.task, res)[1], 0)

    def test_olympiad_submission_scores_by_groups(self):
        olymp = Exam.objects.create(kind='OLYM', title='О', school_class='', duration_minutes=60,
                                    start_at=timezone.now() - timedelta(minutes=1), created_by=self.teacher)
        olymp.tasks.set([self.task]); olymp.participants.add(self.students[0])
        ExamAttempt.objects.create(exam=olymp, student=self.students[0], started_at=timezone.now())
        self.client.force_login(self.students[0].user)
        # ошибка с числами больше 9 (пишет только последнюю цифру): группы 1 и 2 — да, 3 — нет
        code = ("s=input().strip();o='';i=0\n"
                "while i<len(s):\n    j=i\n    while j<len(s) and s[j]==s[i]: j+=1\n"
                "    n=j-i; o+=s[i]+(str(n%10) if n>1 else ''); i=j\nprint(o)")
        self.client.post(reverse('exam_task', args=[olymp.id, self.task.id]), {'code': code})
        ans = ExamAnswer.objects.get(task=self.task)
        self.assertEqual(ans.best_score, 70)
        page = self.client.get(reverse('exam_task', args=[olymp.id, self.task.id]), headers={'Sec-Fetch-Dest': 'iframe'})
        self.assertContains(page, '70/100')
        self.assertContains(page, '0/30')

    def test_edit_form_keeps_group_tests(self):
        self.client.force_login(self.teacher_user)
        url = reverse('task_edit', args=[self.task.id])
        self.assertContains(self.client.get(url), 'Олимпиадная задача')
        self.client.post(url, {'title': 'Олимп. Сжатие', 'description': 'новое', 'input_example': 'abc',
                               'output_example': 'abc', 'hidden': '1'})
        self.task.refresh_from_db()
        self.assertEqual(self.task.description, 'новое')
        self.assertEqual(len(self.task.test_cases), 8)
        self.assertEqual(self.task.test_cases[-1]['group'], 3)

    def test_import_with_groups_and_time_limit(self):
        import json
        self.client.force_login(self.teacher_user)
        item = {'title': 'Импорт группы', 'level': 'C', 'description': 'd', 'input_example': '1', 'output_example': '1',
                'hidden': True, 'test_groups': [40, 60], 'time_limit': 2,
                'test_cases': [{'input': '1', 'output': '1', 'group': 0}, {'input': '2', 'output': '2', 'group': 1},
                               {'input': '3', 'output': '3', 'group': 2}]}
        self.client.post(reverse('task_import'), {'json_text': json.dumps([item])})
        t = Task.objects.get(title='Импорт группы')
        self.assertEqual((t.test_groups, t.time_limit, t.hidden), ([40, 60], 2, True))
        self.assertEqual([c['group'] for c in t.test_cases], [0, 1, 2])
        bad = dict(item, title='Без группы 2', test_cases=item['test_cases'][:2])
        self.client.post(reverse('task_import'), {'json_text': json.dumps([bad])})
        self.assertFalse(Task.objects.filter(title='Без группы 2').exists())


class OlympiadSectionTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.olymp = Exam.objects.create(kind='OLYM', title='Весенняя', school_class='', duration_minutes=60,
                                         start_at=timezone.now() + timedelta(days=1), created_by=self.teacher)
        self.client.force_login(self.teacher_user)

    def item(self, title, **kw):
        d = {'title': title, 'level': 'C', 'description': 'd', 'input_example': '1', 'output_example': '1',
             'test_groups': [40, 60], 'time_limit': 2,
             'test_cases': [{'input': '1', 'output': '1', 'group': 0}, {'input': '2', 'output': '2', 'group': 1},
                            {'input': '3', 'output': '3', 'group': 2}]}
        d.update(kw)
        return d

    def test_teacher_section_lists_olympiads_not_exams(self):
        page = self.client.get(reverse('olympiad_list'))
        self.assertContains(page, 'Весенняя')
        self.assertNotContains(page, 'СОР 1')
        self.assertNotContains(self.client.get(reverse('exam_list')), 'Весенняя')

    def test_create_olympiad_without_tasks(self):
        start = (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('exam_create'), {'kind': 'OLYM', 'title': 'Пустая', 'start_at': start, 'duration_minutes': '90'})
        self.assertTrue(Exam.objects.filter(title='Пустая', kind='OLYM').exists())

    def test_json_upload_adds_hidden_tasks_to_olympiad(self):
        import json
        url = reverse('olympiad_tasks', args=[self.olymp.id])
        r = self.client.post(url, {'action': 'import', 'json_text': json.dumps([self.item('O-1'), self.item('O-2')])})
        self.assertRedirects(r, url)
        self.assertEqual(sorted(self.olymp.tasks.values_list('title', flat=True)), ['O-1', 'O-2'])
        t = Task.objects.get(title='O-1')
        self.assertTrue(t.hidden)
        self.assertEqual((t.test_groups, t.time_limit), ([40, 60], 2))
        # повторная загрузка того же файла в другую олимпиаду — задачи берутся с сайта, дубликатов нет
        other = Exam.objects.create(kind='OLYM', title='Вторая', school_class='', duration_minutes=60,
                                    start_at=timezone.now() + timedelta(days=1), created_by=self.teacher)
        self.client.post(reverse('olympiad_tasks', args=[other.id]), {'action': 'import', 'json_text': json.dumps([self.item('O-1')])})
        self.assertEqual(Task.objects.filter(title='O-1').count(), 1)
        self.assertEqual(list(other.tasks.values_list('title', flat=True)), ['O-1'])
        self.assertContains(self.client.get(url), 'O-2')
        # убрать задачу из олимпиады
        self.client.post(url, {'action': 'remove', 'task': t.id})
        self.assertEqual(list(self.olymp.tasks.values_list('title', flat=True)), ['O-2'])

    def test_upload_from_file_and_errors(self):
        import io, json
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = reverse('olympiad_tasks', args=[self.olymp.id])
        f = SimpleUploadedFile('o.json', json.dumps([self.item('F-1')]).encode(), content_type='application/json')
        self.client.post(url, {'action': 'import', 'json_file': f})
        self.assertTrue(self.olymp.tasks.filter(title='F-1').exists())
        self.client.post(url, {'action': 'import', 'json_text': json.dumps([self.item('Bad', test_groups=[1, 2, 3])])})
        self.assertFalse(Task.objects.filter(title='Bad').exists())          # нет тестов группы 3

    def test_no_changes_after_start(self):
        import json
        self.olymp.start_at = timezone.now() - timedelta(minutes=1); self.olymp.save()
        self.client.post(reverse('olympiad_tasks', args=[self.olymp.id]), {'action': 'import', 'json_text': json.dumps([self.item('Late')])})
        self.assertFalse(Task.objects.filter(title='Late').exists())

    def test_student_cannot_open_tasks_page(self):
        self.client.force_login(self.students[0].user)
        r = self.client.get(reverse('olympiad_tasks', args=[self.olymp.id]))
        self.assertNotEqual(r.status_code, 200)


class OlympiadCppTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            title='Сумма C++', level='C', description='d', input_example='2 3', output_example='5', hidden=True,
            test_groups=[50, 50], test_cases=[{'input': '2 3', 'output': '5', 'group': 0},
                                               {'input': '1 1', 'output': '2', 'group': 1},
                                               {'input': '2000000000 2000000000', 'output': '4000000000', 'group': 2}])
        self.olymp = Exam.objects.create(kind='OLYM', title='О++', school_class='', duration_minutes=60,
                                         start_at=timezone.now() - timedelta(minutes=1), created_by=self.teacher)
        self.olymp.tasks.set([self.task]); self.olymp.participants.add(self.students[0])
        ExamAttempt.objects.create(exam=self.olymp, student=self.students[0], started_at=timezone.now())
        self.client.force_login(self.students[0].user)
        self.url = reverse('exam_task', args=[self.olymp.id, self.task.id])

    def post(self, code, lang):
        from django.core.cache import cache
        cache.clear()
        return self.client.post(self.url, {'code': code, 'language': lang})

    def test_language_choice_shown_only_on_olympiad(self):
        page = self.client.get(self.url, headers={'Sec-Fetch-Dest': 'iframe'})
        self.assertContains(page, 'id="lang-select"')
        self.assertContains(page, 'C++ 17')
        self.assertNotContains(page, 'CPP_TEMPLATE')

    def test_cpp_solution_scores(self):
        from .autotest import cpp_available
        if not cpp_available():
            self.skipTest('нет g++')
        int_code = '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}'
        self.post(int_code, 'cpp')
        ans = ExamAnswer.objects.get(task=self.task)
        self.assertEqual((ans.best_score, ans.language), (50, 'cpp'))       # int переполняется в группе 2
        self.post('#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b;}', 'cpp')
        ans.refresh_from_db()
        self.assertEqual((ans.best_score, ans.best_language), (100, 'cpp'))

    def test_compile_error_is_shown_and_counts_as_submission(self):
        from .autotest import cpp_available
        if not cpp_available():
            self.skipTest('нет g++')
        self.post('int main() { return x; }', 'cpp')
        ans = ExamAnswer.objects.get(task=self.task)
        self.assertEqual(ans.best_score, 0)
        self.assertIn('not declared', ans.compile_error)
        page = self.client.get(self.url, headers={'Sec-Fetch-Dest': 'iframe'})
        self.assertContains(page, 'Ошибка компиляции')

    def test_python_still_default(self):
        self.post('a,b=map(int,input().split());print(a+b)', 'python')
        self.assertEqual(ExamAnswer.objects.get(task=self.task).best_score, 100)

    def test_sor_ignores_language(self):
        ExamAttempt.objects.create(exam=self.exam, student=self.students[0], started_at=timezone.now(),
                                   finished_at=None) if False else None
        from .exam_views import max_submissions
        self.assertEqual(max_submissions(self.exam), 3)

    def test_olympiad_form_has_no_task_list_and_keeps_tasks(self):
        self.olymp.start_at = timezone.now() + timedelta(days=1); self.olymp.save()
        self.client.force_login(self.teacher_user)
        page = self.client.get(reverse('exam_edit', args=[self.olymp.id]))
        self.assertNotContains(page, 'name="tasks"')
        self.assertNotContains(page, 'task-search')
        start = timezone.localtime(self.olymp.start_at).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('exam_edit', args=[self.olymp.id]),
                         {'kind': 'OLYM', 'mode': 'fixed', 'title': 'О++ новое', 'start_at': start, 'duration_minutes': '90'})
        self.olymp.refresh_from_db()
        self.assertEqual(self.olymp.title, 'О++ новое')
        self.assertEqual(list(self.olymp.tasks.all()), [self.task])


class OlympiadHiddenEverywhereTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.otag = Tag.objects.create(name='олимпиада 7–8')
        self.secret = make_task('Секретная олимп', 'C', self.otag)
        self.secret.hidden = True; self.secret.save()
        self.mixed = Tag.objects.create(name='смешанный')            # тег и с обычной, и со скрытой задачей — виден
        make_task('Обычная', 'A', self.mixed)
        self.secret.tags.add(self.mixed)

    def test_visible_tags(self):
        names = set(Tag.visible().values_list('name', flat=True))
        self.assertNotIn('олимпиада 7–8', names)
        self.assertIn('смешанный', names)
        self.assertIn('циклы', names)

    def test_catalog_hides_task_and_tag_for_student_and_teacher(self):
        for user in (self.students[0].user, self.teacher_user):
            self.client.force_login(user)
            html = self.client.get(reverse('all_tasks')).content.decode()
            self.assertNotIn('Секретная олимп', html)
            self.assertNotIn('олимпиада 7–8', html)

    def test_teacher_lists_and_pickers_hide_olympiad(self):
        self.client.force_login(self.teacher_user)
        html = self.client.get(reverse('task_tags_list')).content.decode()
        self.assertNotIn('Секретная олимп', html)
        self.assertNotIn('олимпиада 7–8', html)
        html = self.client.get(reverse('exam_create')).content.decode()        # форма СОР
        self.assertNotIn('Секретная олимп', html)
        self.assertNotIn('олимпиада 7–8', html)

    def test_olympiad_task_edit_keeps_its_tag(self):
        self.client.force_login(self.teacher_user)
        html = self.client.get(reverse('task_edit', args=[self.secret.id])).content.decode()
        self.assertIn('олимпиада 7–8', html)
        other = Task.objects.get(title='Обычная')
        self.assertNotIn('олимпиада 7–8', self.client.get(reverse('task_edit', args=[other.id])).content.decode())


class RunCppTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        u = User.objects.create_user('cpp', password='x')
        Student.objects.create(user=u, full_name='C++ Ученик', school_class='7F')
        self.client.force_login(u)

    def test_run_compile_error_and_throttle(self):
        from .autotest import cpp_available
        if not cpp_available():
            self.skipTest('нет g++')
        from django.core.cache import cache
        url = reverse('run_cpp')
        d = self.client.post(url, {'code': '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a*b;}', 'stdin': '6 7'}).json()
        self.assertEqual((d['out'].strip(), d['compile_error']), ('42', ''))
        self.assertIn('wait', self.client.post(url, {'code': 'int main(){}', 'stdin': ''}).json())   # не чаще раза в 5 с
        cache.clear()
        d = self.client.post(url, {'code': 'int main(){ return y; }', 'stdin': ''}).json()
        self.assertIn('not declared', d['compile_error'])
        cache.clear()
        d = self.client.post(url, {'code': 'int main(){ while(1){} }', 'stdin': ''}).json()
        self.assertEqual(d['error'], 'timeout')

    def test_needs_login_and_post(self):
        self.assertEqual(self.client.get(reverse('run_cpp')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('run_cpp'), {'code': 'x'}).status_code, 302)


class OlympiadWithoutFullscreenTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.task = make_task('Олимп-без-экрана', 'C', self.tag1)
        self.olymp = Exam.objects.create(kind='OLYM', title='Свободная', school_class='', duration_minutes=60,
                                         start_at=timezone.now() - timedelta(minutes=1), created_by=self.teacher)
        self.olymp.tasks.set([self.task]); self.olymp.participants.add(self.students[0])
        self.client.force_login(self.students[0].user)

    def test_start_page_has_no_fullscreen_rules(self):
        html = self.client.get(reverse('exam_detail', args=[self.olymp.id])).content.decode()
        self.assertNotIn('СОР/СОЧ проходит в полноэкранном режиме', html)
        self.assertIn('любом удобном компиляторе', html)

    def test_pages_open_directly_without_shell_and_without_run_button(self):
        ExamAttempt.objects.create(exam=self.olymp, student=self.students[0], started_at=timezone.now())
        r = self.client.get(reverse('exam_detail', args=[self.olymp.id]), headers={'Sec-Fetch-Dest': 'document'})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('location.replace', r.content.decode())
        r = self.client.get(reverse('exam_task', args=[self.olymp.id, self.task.id]), headers={'Sec-Fetch-Dest': 'document'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'olymp-code')                  # простое поле для кода, без редактора и «Запустить»
        self.assertNotContains(r, 'code-editor')
        self.assertContains(r, 'lang-select')
        self.assertContains(r, 'class="col-12"')             # условие на всю ширину, ниже — язык и отправка
        self.assertContains(r, '10 раз<')
        self.assertRedirects(self.client.get(reverse('exam_run', args=[self.olymp.id])),
                             reverse('exam_detail', args=[self.olymp.id]))


class OlympiadExamplesAndGroupsTableTests(RetakeTestBase):
    def item(self, **kw):
        d = {'title': 'Олимп. Тест', 'level': 'C', 'description': 'Условие', 'input_example': '1', 'output_example': '1',
             'examples': [{'input': 'a1', 'output': 'b1', 'note': 'первый'}, {'input': 'a2', 'output': 'b2'},
                          {'input': 'a3', 'output': 'b3', 'note_kk': 'үшінші'}],
             'group_notes': ['N ≤ 10', 'N ≤ 1000'], 'group_notes_kk': ['N ≤ 10 (кк)', 'N ≤ 1000 (кк)'],
             'test_groups': [40, 60], 'time_limit': 2,
             'test_cases': [{'input': '1', 'output': '1', 'group': 0}, {'input': '2', 'output': '2', 'group': 1},
                            {'input': '3', 'output': '3', 'group': 2}]}
        d.update(kw)
        return d

    def setUp(self):
        super().setUp()
        import json
        self.olymp = Exam.objects.create(kind='OLYM', title='Табл', school_class='', duration_minutes=60,
                                         start_at=timezone.now() + timedelta(days=1), created_by=self.teacher)
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('olympiad_tasks', args=[self.olymp.id]), {'action': 'import', 'json_text': json.dumps([self.item()])})
        self.task = Task.objects.get(title='Олимп. Тест')

    def test_all_examples_and_groups_table_render(self):
        self.olymp.start_at = timezone.now() - timedelta(minutes=1); self.olymp.save()
        self.olymp.participants.add(self.students[0])
        ExamAttempt.objects.create(exam=self.olymp, student=self.students[0], started_at=timezone.now())
        self.client.force_login(self.students[0].user)
        html = self.client.get(reverse('exam_task', args=[self.olymp.id, self.task.id])).content.decode()
        for x in ('a1', 'b1', 'a2', 'a3', 'b3', 'первый', 'tx-grid', 'N ≤ 1000', '>60<', '2 с на тест'):
            self.assertIn(x, html)
        self.assertNotIn('Пример ввода (Input)', html)
        self.client.get(reverse('set_content_lang', args=['kk']))
        html = self.client.get(reverse('exam_task', args=[self.olymp.id, self.task.id])).content.decode()
        self.assertIn('үшінші', html)
        self.assertIn('N ≤ 1000 (кк)', html)

    def test_reupload_updates_olympiad_task(self):
        import json
        self.client.post(reverse('olympiad_tasks', args=[self.olymp.id]),
                         {'action': 'import', 'json_text': json.dumps([self.item(description='Новое условие', group_notes=['x', 'y'])])})
        self.assertEqual(Task.objects.filter(title='Олимп. Тест').count(), 1)
        self.task.refresh_from_db()
        self.assertEqual((self.task.description, self.task.group_notes), ('Новое условие', ['x', 'y']))
        self.assertTrue(self.task.hidden)

    def test_plain_task_keeps_two_example_boxes(self):
        self.client.force_login(self.students[0].user)
        html = self.client.get(reverse('task_detail', args=[self.a_tasks[0].id])).content.decode()
        self.assertIn('Пример ввода (Input)', html)
        self.assertNotIn('tx-grid', html)


class OlympiadReplaceTasksTests(RetakeTestBase):
    def test_replace_removes_old_tasks_from_olympiad(self):
        import json
        olymp = Exam.objects.create(kind='OLYM', title='Замена', school_class='', duration_minutes=60,
                                    start_at=timezone.now() + timedelta(days=1), created_by=self.teacher)
        self.client.force_login(self.teacher_user)
        url = reverse('olympiad_tasks', args=[olymp.id])
        item = lambda t: {'title': t, 'level': 'C', 'description': 'd', 'input_example': '1', 'output_example': '1',
                          'test_cases': [{'input': '1', 'output': '1'}]}
        self.client.post(url, {'action': 'import', 'json_text': json.dumps([item('A. Шахматы'), item('B. Сад'), item('C. Принтеры')])})
        self.client.post(url, {'action': 'import', 'replace': '1',
                               'json_text': json.dumps([item('A. Прямоугольники'), item('B. Акции'), item('C. Принтеры')])})
        self.assertEqual(sorted(olymp.tasks.values_list('title', flat=True)), ['A. Прямоугольники', 'B. Акции', 'C. Принтеры'])
        self.assertEqual(Task.objects.filter(title='C. Принтеры').count(), 1)
        self.assertTrue(Task.objects.get(title='A. Шахматы').hidden)      # старая задача осталась на сайте скрытой


class NavProfileLinkTests(TestCase):
    def test_name_links_to_profile_with_avatar_and_banner(self):
        u = User.objects.create_user('navst', password='x')
        st = Student.objects.create(user=u, full_name='Абаев Тимур', school_class='7A')
        st.name_banner = 'fire'; st.save()
        self.client.force_login(u)
        html = self.client.get(reverse('all_tasks')).content.decode()
        nav = html[html.index('<nav'):html.index('</nav>')]
        self.assertIn('class="nav-me"', nav)
        self.assertIn(f'href="{reverse("profile")}" class="nav-me"', nav)
        self.assertIn('pf-avatar', nav)
        self.assertIn('nb b-fire', nav)
        self.assertNotIn('>Профиль<', nav)


class OlympiadLateParticipantTests(RetakeTestBase):
    def test_add_after_start_and_student_can_begin(self):
        task = make_task('Поздняя', 'C', self.tag1)
        olymp = Exam.objects.create(kind='OLYM', title='Уже идёт', school_class='', duration_minutes=120,
                                    start_at=timezone.now() - timedelta(minutes=30), created_by=self.teacher)
        olymp.tasks.set([task])
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('olympiad_participants', args=[olymp.id]), {'action': 'add', 'student': self.students[2].id})
        self.assertTrue(olymp.participants.filter(id=self.students[2].id).exists())
        self.client.force_login(self.students[2].user)
        self.assertContains(self.client.get(reverse('olympiad_list')), 'Уже идёт')
        self.client.post(reverse('exam_start', args=[olymp.id]))
        self.assertTrue(ExamAttempt.objects.filter(exam=olymp, student=self.students[2]).exists())
        self.assertContains(self.client.get(reverse('exam_detail', args=[olymp.id])), 'до 100 баллов')


class OlympiadNoPlaceForStudentTests(RetakeTestBase):
    def test_finished_olympiad_shows_score_but_not_place(self):
        task = make_task('Место', 'C', self.tag1)
        olymp = Exam.objects.create(kind='OLYM', title='Прошла', school_class='', duration_minutes=60,
                                    start_at=timezone.now() - timedelta(hours=3), created_by=self.teacher)
        olymp.tasks.set([task]); olymp.participants.add(self.students[0])
        a = ExamAttempt.objects.create(exam=olymp, student=self.students[0], started_at=timezone.now() - timedelta(hours=3))
        ExamAnswer.objects.create(attempt=a, task=task, best_score=70, submissions_count=1, submitted_at=timezone.now())
        self.client.force_login(self.students[0].user)
        html = self.client.get(reverse('exam_detail', args=[olymp.id])).content.decode()
        self.assertIn('70 / 100', html)
        self.assertNotIn('место из', html)
        self.assertNotIn('🥇', html)
        self.client.force_login(self.teacher_user)                 # у учителя места остаются
        self.assertContains(self.client.get(reverse('exam_results', args=[olymp.id])), '🥇')


class ExamPreviewTests(RetakeTestBase):
    def test_teacher_previews_sor_and_checks_without_saving(self):
        self.client.force_login(self.teacher_user)
        self.assertContains(self.client.get(reverse('exam_list') + f'?cls={self.exam.school_class}'), reverse('exam_preview', args=[self.exam.id]))
        page = self.client.get(reverse('exam_preview', args=[self.exam.id]))
        self.assertContains(page, 'Предпросмотр')
        task = page.context['tasks'][0]
        self.assertEqual(task.letter, 'A')
        url = reverse('exam_preview_task', args=[self.exam.id, task.id])
        page = self.client.get(url)
        self.assertContains(page, 'code-editor')
        self.assertContains(page, task.title)
        r = self.client.post(url, {'code': 'print(input())'})
        self.assertIsNotNone(r.context['check'])
        self.assertContains(r, 'Проверка (не сохраняется)')
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam).exists())
        self.assertFalse(ExamAnswer.objects.exists())

    def test_olympiad_preview_has_language_and_plain_field(self):
        task = Task.objects.create(title='Олимп П', level='C', description='d', input_example='1', output_example='1',
                                   hidden=True, test_groups=[100], test_cases=[{'input': '1', 'output': '1', 'group': 1}])
        olymp = Exam.objects.create(kind='OLYM', title='Пред', school_class='', duration_minutes=60,
                                    start_at=timezone.now() + timedelta(days=1), created_by=self.teacher)
        olymp.tasks.set([task])
        self.client.force_login(self.teacher_user)
        self.assertContains(self.client.get(reverse('olympiad_list')), reverse('exam_preview', args=[olymp.id]))
        url = reverse('exam_preview_task', args=[olymp.id, task.id])
        page = self.client.get(url)
        self.assertContains(page, 'lang-select')
        self.assertNotContains(page, 'code-editor')
        r = self.client.post(url, {'code': 'print(input())', 'language': 'python'})
        self.assertEqual(r.context['check']['score'], 100)
        self.assertEqual([g['got'] for g in r.context['check']['groups']], [100])

    def test_students_cannot_preview(self):
        self.client.force_login(self.students[0].user)
        self.assertNotEqual(self.client.get(reverse('exam_preview', args=[self.exam.id])).status_code, 200)


class SubmitCooldownTests(RetakeTestBase):
    def test_minute_cooldown_keeps_editor_open(self):
        from .models import Submission
        from .views import SUBMISSION_COOLDOWN
        self.assertEqual(SUBMISSION_COOLDOWN, timedelta(minutes=1))
        st, task = self.students[0], self.a_tasks[0]
        self.client.force_login(st.user)
        url = reverse('task_detail', args=[task.id])
        self.client.post(url, {'code': 'print("не то")'})
        html = self.client.get(url).content.decode()
        self.assertIn('id="cooldown-box"', html)
        area = html[html.index('<textarea name="code"'):html.index('</textarea>')]
        self.assertNotIn('disabled', area)                         # писать код можно сразу
        self.assertIn('id="task-submit" class="btn btn-pifagor-green px-4 py-2 rounded-3" disabled', html)
        sub = Submission.objects.get(student=st, task=task)
        Submission.objects.filter(pk=sub.pk).update(last_submitted_at=timezone.now() - timedelta(seconds=61))
        html = self.client.get(url).content.decode()               # через минуту отправка снова открыта
        self.assertNotIn('id="cooldown-box"', html)


class ExamsByClassAndQuarterTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.teacher_user)

    def test_first_page_is_classes_then_quarters(self):
        html = self.client.get(reverse('exam_list')).content.decode()
        self.assertIn('?cls=7F', html)
        self.assertNotIn(self.exam.title, html)                     # сам список — внутри класса
        r = self.client.get(reverse('exam_list') + '?cls=7F')
        self.assertEqual([b['n'] for b in r.context['blocks']], [1, 2, 3, 4])
        self.assertContains(r, self.exam.title)
        self.assertContains(r, 'quarter=3&kind=SOCH')

    def test_create_without_title_by_quarter_and_kind(self):
        start = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        data = {'kind': 'SOCH', 'quarter': '3', 'school_class': '7F', 'start_at': start, 'duration_minutes': '40',
                'tasks': [self.a_tasks[0].id]}
        page = self.client.get(reverse('exam_create') + '?cls=7F&quarter=3&kind=SOCH')
        self.assertNotContains(page, 'name="title"')
        self.assertEqual((page.context['v']['quarter'], page.context['v']['kind'], page.context['v']['school_class']), (3, 'SOCH', '7F'))
        r = self.client.post(reverse('exam_create'), data)
        self.assertRedirects(r, reverse('exam_list') + '?cls=7F')
        ex = Exam.objects.get(kind='SOCH', quarter=3)
        self.assertEqual(ex.title, 'СОЧ (3 четверть)')
        self.client.post(reverse('exam_create'), data)               # второй такой же — с номером
        self.assertTrue(Exam.objects.filter(title='СОЧ №2 (3 четверть)').exists())
        blocks = self.client.get(reverse('exam_list') + '?cls=7F').context['blocks']
        self.assertEqual(len(blocks[2]['exams']), 2)

    def test_old_exam_gets_quarter_by_date(self):
        from .exam_views import exam_quarter
        self.exam.quarter = None
        self.exam.start_at = timezone.make_aware(timezone.datetime(2026, 10, 6, 10, 0))
        self.assertEqual(exam_quarter(self.exam), 1)
        self.exam.start_at = timezone.make_aware(timezone.datetime(2026, 11, 20, 10, 0))
        self.assertEqual(exam_quarter(self.exam), 2)


class AdminDashboardTests(RetakeTestBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('boss', password='x')
        now = timezone.now()
        self.attempt(self.exam, self.students[0], 10)        # СОР: 10
        self.attempt(self.exam, self.students[1], 6)         # СОР: 6
        self.attempt(self.random_exam, self.students[2], 8,
                     task_ids=[self.a_tasks[1].id, self.b_tasks[1].id])   # СОЧ: 8
        for st, score in ((self.students[0], 10), (self.students[1], 7), (self.other_class, 10)):
            Submission.objects.create(student=st, task=self.a_tasks[0], score=score, status='DONE', is_graded=True)
        LoginEvent.objects.create(user=self.students[0].user)
        LoginEvent.objects.create(user=self.students[0].user)
        LoginEvent.objects.create(user=self.other_class.user)

    def test_only_admin_sees_it(self):
        url = reverse('admin_dashboard')
        self.assertEqual(self.client.get(url).status_code, 302)                  # аноним
        self.client.force_login(self.students[0].user)
        self.assertEqual(self.client.get(url).status_code, 302)                  # ученик
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_quarters_chart_data_for_class_and_school(self):
        from .dashboard import build
        Exam.objects.filter(id=self.exam.id).update(quarter=1)
        Exam.objects.filter(id=self.random_exam.id).update(quarter=2)
        d = build()
        self.assertEqual([(q['n'], q['sor'], q['soch']) for q in d['quarters']], [(1, 8.0, None), (2, None, 8.0)])
        self.assertEqual((d['kpi']['sor'], d['kpi']['soch']), (8.0, 8.0))            # итого по году не фильтруется
        cls = self.students[0].school_class
        one = build(cls=cls)                                                         # выбран класс — те же данные только по нему
        self.assertEqual(one['mode'], 'student')
        self.assertEqual([q['n'] for q in one['quarters']], [1, 2])
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_dashboard'))
        self.assertContains(r, 'Средний балл СОР и СОЧ — по четвертям')
        self.assertNotContains(r, 'СОР/СОЧ за:')                                     # фильтра по четвертям нет

    def test_teacher_sees_only_own_classes(self):
        from .dashboard import build
        url = reverse('admin_dashboard')
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(url).status_code, 200)
        classes = {s.school_class for s in self.students}
        mine = sorted(classes)[0]
        d = build(classes={mine})
        self.assertEqual(d['mode'], 'class')
        self.assertTrue(d['scoped'])
        self.assertEqual([r['name'] for r in d['rows']], [mine])
        self.assertLess(d['kpi']['students'], build()['kpi']['students'])
        one = build(classes={mine}, cls=mine)
        self.assertEqual(one['mode'], 'student')
        self.assertEqual(sum(r['students'] for r in one['rows']), one['kpi']['students'])
        other = build(classes={mine}, cls='8Z')                                  # чужой класс игнорируется
        self.assertEqual(other['mode'], 'class')

    def test_numbers_all_parallels(self):
        from .dashboard import build
        d = build()
        k = d['kpi']
        self.assertEqual((k['students'], k['active'], k['logins']), (4, 2, 3))
        self.assertEqual(k['solved'], 2)                                         # два «10 из 10»
        self.assertEqual(k['avg_score'], 9.0)                                    # (10+7+10)/3
        self.assertEqual((k['sor'], k['soch'], k['grade']), (8.0, 8.0, 8.0))
        self.assertEqual(d['parallels'], ['7', '8'])
        rows = {r['name']: r for r in d['rows']}
        self.assertEqual((rows['7']['students'], rows['7']['active'], rows['7']['solved']), (3, 1, 1))
        self.assertEqual((rows['7']['sor'], rows['7']['soch']), (8.0, 8.0))
        self.assertEqual((rows['8']['students'], rows['8']['solved'], rows['8']['sor']), (1, 1, None))
        self.assertEqual(sum(x['count'] for x in d['dist']), 3)
        self.assertEqual(sum(d['week_solved']), 2)

    def test_parallel_filter_goes_by_classes(self):
        from .dashboard import build
        d = build('7')
        self.assertEqual(d['mode'], 'class')
        self.assertEqual([r['name'] for r in d['rows']], ['7F'])
        self.assertEqual(d['kpi']['students'], 3)
        self.assertEqual(d['kpi']['solved'], 1)
        self.assertEqual(build('99')['mode'], 'parallel')                    # нет такой параллели — все

    def test_page_renders_with_filter_and_empty_db(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_dashboard') + '?p=7')
        self.assertContains(r, '7F')
        self.assertContains(r, 'dash-data')
        ExamAttempt.objects.all().delete(); Submission.objects.all().delete(); LoginEvent.objects.all().delete()
        self.assertEqual(self.client.get(reverse('admin_dashboard')).status_code, 200)

    def test_everyone_gets_link_in_cabinet(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('teacher_dashboard')), reverse('admin_dashboard'))
        self.client.force_login(self.teacher_user)
        self.assertContains(self.client.get(reverse('teacher_dashboard')), reverse('admin_dashboard'))


class IslandGameTests(TestCase):
    """«Битва за остров»: лобби, команды, карта, захват клеток, задачи двух видов, выходы, монеты."""

    def setUp(self):
        cache.clear()
        from .models import IslandGame
        self.tu = User.objects.create_user('is_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='7A')
        self.sts = [Student.objects.create(user=User.objects.create_user(f'is_s{i}', password='p'),
                                           full_name=f'Ученик Номер{i}', school_class='7A') for i in range(6)]
        self.foreign = Student.objects.create(user=User.objects.create_user('is_f', password='p'), full_name='Чужой', school_class='9Z')
        tag = Tag.objects.create(name='остров')
        self.code_task = make_task('Код A', 'A', tag)
        self.game = IslandGame(owner=self.tu, title='Остров', mode='teams', teams_count=3, minutes=40)
        self.game.set_classes(['7A'])
        self.game.save()
        self.game.tags.add(tag)

    def _run(self, n=6):
        from . import island
        import random
        for st in self.sts[:n]:
            island.join(self.game, st)
        island.shuffle_teams(self.game, random.Random(1))
        self.game = island.start(self.game, random.Random(2))
        return island

    def test_switch_language_in_game_without_reload(self):
        self._run()
        st = self.sts[0]
        self.client.force_login(st.user)
        html = self.client.get(reverse('island_play', args=[self.game.id])).content.decode()
        self.assertIn('data-l="kk"', html)
        self.assertIn('data-tr="Очки"', html)
        p = self.game.players.get(student=st)
        exits = p.exits
        r = self.client.post(reverse('island_lang', args=[self.game.id]), {'lang': 'kk', 'keys': '["Очки", "Выходы"]'}).json()
        self.assertTrue(r['ok'])
        self.assertEqual(r['tr']['Выходы'], UI_TRANSLATIONS.get('Выходы', 'Выходы'))
        self.assertEqual(r['txt']['started'], UI_TRANSLATIONS['Игра началась!'])
        self.assertEqual(self.client.session['content_lang'], 'kk')
        p.refresh_from_db()
        self.assertEqual(p.exits, exits)                                     # смена языка — не выход из игры
        r = self.client.post(reverse('island_lang', args=[self.game.id]), {'lang': 'ru', 'keys': '["Очки"]'}).json()
        self.assertEqual(r['tr'], {'Очки': 'Очки'})

    def _neutral_neighbour(self, island, p, kind):
        owned = {(c.x, c.y) for c in self.game.cells.all()}
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (p.x + dx, p.y + dy)
            if c not in owned and island.terrain(self.game, *c) == kind:
                return c
        return None

    def _player_with(self, island, kind):
        """Игрок, рядом с которым есть ничья клетка нужной местности (иначе местность подставляем сами)."""
        for p in self.game.players.all():
            c = self._neutral_neighbour(island, p, kind)
            if c:
                return p, c
        p = self.game.players.first()
        owned = {(c.x, c.y) for c in self.game.cells.all()}
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            x, y = p.x + dx, p.y + dy
            if (x, y) not in owned and island.terrain(self.game, x, y) != island.WATER:
                rows = [list(r) for r in self.game.map['rows']]
                rows[y][x] = kind
                self.game.map['rows'] = [''.join(r) for r in rows]
                self.game.save(update_fields=['map'])
                return p, (x, y)
        self.fail('нет свободной соседней клетки')

    def test_teacher_creates_game_and_students_see_it(self):
        from .models import IslandGame
        self.client.login(username='is_t', password='p')
        r = self.client.post(reverse('island_manage'), {'classes': ['7A', '9Z'], 'mode': 'teams', 'teams_count': 9, 'minutes': 40, 'topics': ['@io']})
        g = IslandGame.objects.latest('id')
        self.assertRedirects(r, reverse('island_control', args=[g.id]))
        self.assertEqual((g.class_list(), g.teams_count, g.status), (['7A'], 6, 'lobby'))      # чужой класс и лишние команды отброшены
        self.assertContains(self.client.get(reverse('games_hub')), 'Битва за остров')
        self.client.login(username='is_s0', password='p')
        self.assertContains(self.client.get(reverse('codegames')), reverse('island_play', args=[g.id]))
        self.assertEqual(self.client.get(reverse('island_play', args=[g.id])).status_code, 200)   # вошёл в лобби
        self.assertEqual(g.players.count(), 1)
        self.assertNotEqual(self.client.get(reverse('island_manage')).status_code, 200)
        self.client.login(username='is_f', password='p')
        self.assertEqual(self.client.get(reverse('island_play', args=[g.id])).status_code, 404)

    def test_shuffle_is_even_and_start_builds_connected_map(self):
        island = self._run()
        players = list(self.game.players.all())
        sizes = sorted([sum(1 for p in players if p.team == t) for t in range(3)])
        self.assertEqual(sizes, [2, 2, 2])
        self.assertEqual(self.game.status, 'running')
        land = island._land(self.game)
        self.assertEqual(len(island._components(land)), 1)                     # вся суша связна
        self.assertGreaterEqual(len(land), 6 * 9)
        cells = {(c.x, c.y): c.team for c in self.game.cells.all()}
        self.assertEqual(len(cells), 6)                                         # у каждого своя стартовая клетка
        for p in players:
            self.assertEqual(cells[(p.x, p.y)], p.team)
        self.assertEqual(sum(self.game.scores.values()), 6)

    def test_output_question_captures_cell_and_wrong_answer_gives_cooldown(self):
        island = self._run()
        p, (x, y) = self._player_with(island, island.GRASS)
        self.assertEqual(island.move(self.game, p, x + 5, y), {'error': 'far'})
        self.assertEqual(island.move(self.game, p, x, y), {'challenge': True})
        pub = island.public_challenge(p)
        self.assertEqual(pub['kind'], 'output')
        self.assertNotIn('answer', pub)
        res = island.answer(self.game, p, 'совсем не то')
        self.assertEqual((res['ok'], res['wait']), (False, island.COOLDOWN))
        self.assertEqual(island.move(self.game, p, x, y)['error'], 'cooldown')
        p.cooldown_until = None; p.save()
        island.move(self.game, p, x, y)
        before = self.game.scores[str(p.team)]
        res = island.answer(self.game, p, p.challenge['answer'])
        self.assertTrue(res['ok'])
        p.refresh_from_db(); self.game.refresh_from_db()
        self.assertEqual(((p.x, p.y), p.captured, p.points), ((x, y), 1, 1))
        self.assertEqual(self.game.scores[str(p.team)], before + 1)
        self.assertEqual(self.game.cells.get(x=x, y=y).team, p.team)
        back = (x - 1, y) if (x - 1, y) in {(c.x, c.y) for c in self.game.cells.filter(team=p.team)} else None
        if back:                                                               # по своим клеткам — без задачи
            self.assertEqual(island.move(self.game, p, *back), {'moved': True})

    def test_forest_needs_code_and_enemy_cell_is_harder(self):
        island = self._run()
        p, (x, y) = self._player_with(island, island.FOREST)
        island.move(self.game, p, x, y)
        self.assertEqual(p.challenge['kind'], 'code')
        self.assertEqual(island.public_challenge(p)['title'], 'Код A')
        bad = island.answer(self.game, p, 'print(2)')
        self.assertEqual((bad['ok'], bad['passed'], bad['total']), (False, 0, 1))
        self.assertEqual(p.challenge['kind'], 'code')                          # задача та же, можно повторить после паузы
        p.cooldown_until = None; p.save()
        self.assertTrue(island.answer(self.game, p, 'print(1)')['ok'])
        self.game.refresh_from_db()
        self.assertEqual(self.game.cells.get(x=x, y=y).team, p.team)
        enemy = self.game.players.exclude(team=p.team).first()
        enemy.x, enemy.y = x + 1, y; enemy.save()                              # сосед встаёт рядом (если там суша)
        if island.terrain(self.game, x + 1, y) != island.WATER:
            self.assertEqual(island.cell_level(self.game, enemy, x, y, p.team), 3)
            island.move(self.game, enemy, x, y)
            self.assertTrue(enemy.challenge['enemy'])
            old = self.game.scores[str(p.team)]
            island.capture(self.game, enemy, enemy.challenge)
            self.game.refresh_from_db()
            self.assertEqual(self.game.scores[str(p.team)], old - 2)           # очки ушли к захватчику
            self.assertEqual(self.game.cells.get(x=x, y=y).team, enemy.team)

    def test_three_exits_kick_but_cells_stay_and_teacher_restores(self):
        island = self._run()
        st = self.sts[0]
        p = self.game.players.get(student=st)
        self.client.login(username='is_s0', password='p')
        url = reverse('island_exit', args=[self.game.id])
        self.assertEqual(self.client.post(url).json()['exits'], 0)             # не вошёл в полный экран — не считается
        self.assertEqual(self.client.post(reverse('island_move', args=[self.game.id]), {'x': p.x + 1, 'y': p.y}).json()['error'], 'notarmed')
        self.assertTrue(self.client.post(reverse('island_arm', args=[self.game.id])).json()['ok'])
        self.assertEqual(self.client.post(url, {'kind': 'blur'}).json()['left'], 2)
        self.client.get(reverse('island_play', args=[self.game.id]))            # обновил страницу — второй выход
        d = self.client.post(url).json()
        self.assertEqual((d['exits'], d['kicked']), (3, True))
        p.refresh_from_db()
        self.assertTrue(p.kicked)
        self.assertEqual(self.game.cells.filter(team=p.team).count(), 2)        # клетки остались команде
        self.assertEqual(island.move(self.game, p, p.x + 1, p.y)['error'], 'kicked')
        self.client.login(username='is_t', password='p')
        self.client.post(reverse('island_control_action', args=[self.game.id]), {'action': 'restore', 'player': p.id})
        p.refresh_from_db()
        self.assertEqual((p.kicked, p.exits), (False, 0))

    def test_finish_awards_coins_and_state_sends_only_changes(self):
        from .models import CoinTransaction
        island = self._run()
        p, (x, y) = self._player_with(island, island.GRASS)
        full = island.state(self.game, 0, me=p)
        self.assertIn('map', full)
        self.assertEqual(len(full['cells']), 6)
        island.move(self.game, p, x, y)
        island.answer(self.game, p, p.challenge['answer'])
        self.game.refresh_from_db()
        diff = island.state(self.game, full['v'], me=p)
        self.assertNotIn('map', diff)
        self.assertEqual(diff['cells'], [[x, y, p.team]])
        self.game.ends_at = timezone.now() - timedelta(seconds=1); self.game.save()
        done = island.state(self.game, diff['v'], me=p)
        self.assertEqual(done['status'], 'finished')
        self.assertEqual(island.move(self.game, p, x, y)['error'], 'over')
        tx = CoinTransaction.objects.filter(key=f'island:{self.game.id}')
        self.assertEqual([t.student_id for t in tx], [p.student_id])            # монеты — только тем, кто захватывал, 1 место +10
        self.assertEqual(tx[0].amount, 10)
        self.assertEqual(island.PRIZES, [10, 7, 5])
        mvp = CoinTransaction.objects.filter(key=f'island-mvp:{self.game.id}')   # MVP — больше всех очков, ещё +5
        self.assertEqual([(t.student_id, t.amount) for t in mvp], [(p.student_id, 5)])
        p.refresh_from_db()
        self.assertEqual(p.coins, 15)
        self.assertEqual(done['mvp'], {'ids': [p.id], 'names': [p.student.full_name], 'points': p.points, 'bonus': 5})
        self.assertGreater(p.points, 0)
        island.finish_if_needed(self.game, force=True)                          # повторный финиш ничего не начисляет
        self.assertEqual(CoinTransaction.objects.filter(key=f'island:{self.game.id}').count(), 1)
        self.assertEqual(CoinTransaction.objects.filter(key=f'island-mvp:{self.game.id}').count(), 1)

    def test_mvp_is_top_scorer_not_kicked(self):
        island = self._run()
        ps = list(self.game.players.all())
        ps[0].points, ps[0].captured = 9, 4
        ps[1].points, ps[1].captured = 9, 5
        ps[2].points, ps[2].captured, ps[2].kicked = 20, 9, True              # выбитый MVP не становится
        self.assertEqual(island.mvp_players(ps), [ps[1]])                        # при равных очках — у кого больше клеток
        ps[0].captured = 5
        self.assertEqual(island.mvp_players(ps), [ps[0], ps[1]])                 # полное равенство — оба
        for q in ps:
            q.points = 0
        self.assertEqual(island.mvp_players(ps), [])

    def test_solo_mode_and_late_join(self):
        from . import island
        import random
        self.game.mode = 'solo'; self.game.save()
        for st in self.sts[:4]:
            island.join(self.game, st)
        self.game = island.start(self.game, random.Random(3))
        self.assertEqual(sorted(p.team for p in self.game.players.all()), [0, 1, 2, 3])
        late = island.join(self.game, self.sts[4])
        self.game.refresh_from_db()
        self.assertEqual(late.team, 4)
        self.assertEqual(self.game.cells.get(x=late.x, y=late.y).team, 4)
        self.assertEqual(len(island.teams_info(self.game, list(self.game.players.select_related('student')))), 5)
        self.client.login(username='is_t', password='p')
        d = self.client.get(reverse('island_control_state', args=[self.game.id])).json()
        self.assertEqual((d['status'], len(d['players'])), ('running', 5))
        self.assertEqual(self.client.get(reverse('island_control', args=[self.game.id])).status_code, 200)


class OlympiadExtraTimeTests(RetakeTestBase):
    """Олимпиада: тем, кто не писал, учитель назначает своё время на ту же олимпиаду."""

    def setUp(self):
        super().setUp()
        self.t1 = make_task('Олимп-1', 'C', self.tag1); self.t2 = make_task('Олимп-2', 'C', self.tag1)
        self.olymp = Exam.objects.create(kind='OLYM', title='Осенняя олимпиада', school_class='',
                                         start_at=timezone.now() - timedelta(hours=3), duration_minutes=60, created_by=self.teacher)
        self.olymp.tasks.set([self.t1, self.t2])
        self.wrote, self.missed, self.missed2 = self.students[0], self.students[1], self.students[2]
        self.olymp.participants.add(self.wrote, self.missed, self.missed2)
        a = ExamAttempt.objects.create(exam=self.olymp, student=self.wrote, started_at=self.olymp.start_at)
        ExamAnswer.objects.create(attempt=a, task=self.t1, best_score=70, submissions_count=1, submitted_at=self.olymp.start_at)
        self.url = reverse('retake_create', args=[self.olymp.id])

    def _assign(self, students, minutes_from_now=-5, duration=60):
        start = timezone.localtime(timezone.now() + timedelta(minutes=minutes_from_now))
        return self.client.post(self.url, {'students': [s.id for s in students],
                                           'start_at': start.strftime('%Y-%m-%dT%H:%M'), 'duration_minutes': duration})

    def test_form_lists_only_those_who_did_not_write(self):
        self.client.force_login(self.teacher_user)
        page = self.client.get(self.url)
        self.assertContains(page, self.missed.full_name)
        self.assertContains(page, self.missed2.full_name)
        self.assertNotContains(page, self.wrote.full_name)
        self.assertContains(self.client.get(reverse('exam_results', args=[self.olymp.id])), 'Дать время несдавшим')
        self.assertContains(self.client.get(reverse('olympiad_list')), self.url)
        r = self._assign([self.wrote])                                    # писавшего назначить нельзя
        self.assertEqual(ExamRetake.objects.filter(exam=self.olymp).count(), 0)
        self._assign([self.missed], minutes_from_now=-200, duration=30)   # время в прошлом — нельзя
        self.assertEqual(ExamRetake.objects.filter(exam=self.olymp).count(), 0)

    def test_not_allowed_while_olympiad_is_running(self):
        self.olymp.start_at = timezone.now() - timedelta(minutes=5); self.olymp.save()
        self.client.force_login(self.teacher_user)
        self.assertRedirects(self.client.get(self.url), reverse('exam_results', args=[self.olymp.id]))

    def test_student_writes_same_olympiad_in_extra_time(self):
        self.client.force_login(self.teacher_user)
        self.assertRedirects(self._assign([self.missed]), reverse('exam_results', args=[self.olymp.id]))
        rt = ExamRetake.objects.get(exam=self.olymp)
        self.assertEqual(list(rt.students.all()), [self.missed])
        self.assertContains(self.client.get(self.url), 'уже назначено')   # второй раз, пока идёт, — нельзя

        self.client.force_login(self.missed.user)
        page = self.client.get(reverse('olympiad_list'))
        self.assertContains(page, reverse('retake_detail', args=[self.olymp.id, rt.id]))
        self.assertContains(page, 'идёт')
        r = self.client.get(reverse('exam_detail', args=[self.olymp.id]))  # старая ссылка ведёт на назначенное время
        self.assertRedirects(r, reverse('retake_detail', args=[self.olymp.id, rt.id]))
        detail = self.client.get(reverse('retake_detail', args=[self.olymp.id, rt.id]))
        self.assertNotContains(detail, 'Пересдача')
        self.assertNotContains(detail, 'другие задачи')
        self.client.post(reverse('retake_start', args=[self.olymp.id, rt.id]))
        att = ExamAttempt.objects.get(exam=self.olymp, student=self.missed)
        self.assertEqual((att.retake_id, att.task_ids), (rt.id, [self.t1.id, self.t2.id]))     # те же задачи
        detail = self.client.get(reverse('retake_detail', args=[self.olymp.id, rt.id]))
        self.assertContains(detail, 'Олимп-1'); self.assertContains(detail, 'Олимп-2')
        self.assertEqual(self.client.get(reverse('retake_task', args=[self.olymp.id, rt.id, self.t1.id])).status_code, 200)
        ExamAnswer.objects.create(attempt=att, task=self.t1, best_score=100, submissions_count=1, submitted_at=timezone.now())
        ExamAnswer.objects.create(attempt=att, task=self.t2, best_score=20, submissions_count=1, submitted_at=timezone.now())

        self.client.force_login(self.missed2.user)                         # не назначен — для него олимпиада закончилась
        self.assertNotContains(self.client.get(reverse('olympiad_list')), 'retake')
        self.assertEqual(self.client.get(reverse('retake_detail', args=[self.olymp.id, rt.id])).status_code, 302)

        self.client.force_login(self.teacher_user)                         # результат — в общей таблице мест
        page = self.client.get(reverse('exam_results', args=[self.olymp.id]))
        rows = {r['student'].id: r for r in page.context['rows']}
        self.assertEqual((rows[self.missed.id]['total'], rows[self.missed.id]['place']), (120, 1))
        self.assertEqual((rows[self.wrote.id]['total'], rows[self.wrote.id]['place']), (70, 2))
        self.assertEqual(rows[self.missed.id]['extra'], rt)
        self.assertIsNone(rows[self.missed2.id]['place'])
        self.assertContains(page, 'Дополнительное время для несдавших')
        self.assertNotContains(self.client.get(self.url), self.missed.full_name + '</strong>')   # он уже писал
        self.client.post(reverse('retake_delete', args=[self.olymp.id, rt.id]))
        self.assertTrue(ExamRetake.objects.filter(id=rt.id).exists())      # уже писали — удалить нельзя

    def test_pending_window_shown_and_can_be_deleted(self):
        self.client.force_login(self.teacher_user)
        self._assign([self.missed, self.missed2], minutes_from_now=60)
        rt = ExamRetake.objects.get(exam=self.olymp)
        page = self.client.get(reverse('exam_results', args=[self.olymp.id]))
        self.assertContains(page, 'назначено на')
        self.client.force_login(self.missed.user)
        lst = self.client.get(reverse('olympiad_list'))
        self.assertContains(lst, 'Скоро')
        self.client.post(reverse('retake_start', args=[self.olymp.id, rt.id]))       # ещё не началось
        self.assertFalse(ExamAttempt.objects.filter(exam=self.olymp, student=self.missed).exists())
        self.client.force_login(self.teacher_user)
        self.client.post(reverse('retake_delete', args=[self.olymp.id, rt.id]))
        self.assertFalse(ExamRetake.objects.filter(id=rt.id).exists())


class OwnAvatarTests(TestCase):
    """«Свой аватар»: покупка за 2500 монет, загрузка фото, показ, удаление учителем."""

    def setUp(self):
        from .models import CoinTransaction
        self.st = Student.objects.create(user=User.objects.create_user('own_s', password='p'), full_name='Аня', school_class='7A')
        CoinTransaction.objects.create(student=self.st, key='seed', kind='task', amount=3000, at=timezone.now())
        self.client.login(username='own_s', password='p')

    def png(self, size=(300, 200), name='me.png'):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        b = BytesIO(); Image.new('RGB', size, (200, 30, 30)).save(b, 'PNG')
        return SimpleUploadedFile(name, b.getvalue(), content_type='image/png')

    def test_prices(self):
        from . import avatars
        self.assertEqual(avatars.OWN_PRICE, 2500)
        self.assertEqual((avatars.BY_KEY['nurmash']['price'], avatars.BY_KEY['ernar']['price']), (1000, 1000))

    def test_upload_requires_purchase(self):
        r = self.client.post(reverse('avatar_upload'), {'photo': self.png()})
        self.assertRedirects(r, reverse('shop'))
        self.assertFalse(hasattr(Student.objects.get(id=self.st.id), 'custom_avatar'))

    def test_buy_upload_and_show(self):
        from . import coins
        self.assertRedirects(self.client.post(reverse('shop_buy'), {'avatar': 'own'}), reverse('avatar_upload'))
        self.assertEqual(coins.balance(Student.objects.get(id=self.st.id)), 500)
        self.client.post(reverse('shop_buy'), {'avatar': 'own'})                      # повторно не списывает
        self.assertEqual(coins.balance(Student.objects.get(id=self.st.id)), 500)
        r = self.client.post(reverse('avatar_upload'), {'photo': self.png()})
        self.assertRedirects(r, reverse('profile'), fetch_redirect_response=False)
        st = Student.objects.get(id=self.st.id)
        self.assertEqual(st.avatar, f'u{st.id}.1')
        img = self.client.get(reverse('custom_avatar_img', args=[st.id, 1]))
        self.assertEqual((img.status_code, img['Content-Type']), (200, 'image/webp'))
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(img.content))
        self.assertEqual(im.size, (256, 256))
        self.assertEqual(im.convert('RGBA').getpixel((0, 0))[3], 0)                  # углы прозрачные — круг
        self.assertContains(self.client.get(reverse('profile')), f'/avatar/{st.id}/1.webp')
        self.client.post(reverse('avatar_upload'), {'photo': self.png()})            # замена увеличивает версию
        self.assertEqual(Student.objects.get(id=self.st.id).avatar, f'u{st.id}.2')

    def test_not_enough_coins(self):
        from .models import CoinTransaction
        CoinTransaction.objects.create(student=self.st, key='spend', kind='spend', amount=-1000, at=timezone.now())
        self.client.post(reverse('shop_buy'), {'avatar': 'own'})
        from . import avatars
        self.assertFalse(avatars.has_own(Student.objects.get(id=self.st.id)))

    def test_bad_file_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.post(reverse('shop_buy'), {'avatar': 'own'})
        self.client.post(reverse('avatar_upload'), {'photo': SimpleUploadedFile('x.png', b'not an image')})
        self.assertFalse(hasattr(Student.objects.get(id=self.st.id), 'custom_avatar'))

    def test_teacher_removes_photo(self):
        self.client.post(reverse('shop_buy'), {'avatar': 'own'})
        self.client.post(reverse('avatar_upload'), {'photo': self.png()})
        t = User.objects.create_user('own_t', password='p', is_staff=True)
        Teacher.objects.create(user=t, full_name='Учитель', classes='7A')
        self.client.login(username='own_t', password='p')
        self.client.post(reverse('student_avatar_remove', args=[self.st.id]))
        st = Student.objects.get(id=self.st.id)
        self.assertEqual(st.avatar, '')
        self.assertFalse(hasattr(st, 'custom_avatar'))
        self.assertIn('own', st.owned_avatars)                                      # покупка осталась


class ShopAdminTests(TestCase):
    """Админ управляет аватарами и баннерами: цены, названия, скрытие, свои аватары."""

    def setUp(self):
        from .models import CoinTransaction
        from . import shop_items
        shop_items.invalidate()
        self.admin = User.objects.create_superuser('sa', password='p')
        self.teacher = User.objects.create_user('st', password='p', is_staff=True)
        self.st = Student.objects.create(user=User.objects.create_user('ss', password='p'), full_name='Аня', school_class='7A')
        CoinTransaction.objects.create(student=self.st, key='seed', kind='task', amount=5000, at=timezone.now())
        self.url = reverse('shop_admin')

    def post(self, **kw):
        from . import shop_items
        r = self.client.post(self.url, kw)
        shop_items.invalidate()
        return r

    def png(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        b = BytesIO(); Image.new('RGB', (300, 300), (10, 120, 200)).save(b, 'PNG')
        return SimpleUploadedFile('a.png', b.getvalue(), content_type='image/png')

    def test_only_superuser(self):
        self.client.login(username='st', password='p')
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.login(username='sa', password='p')
        self.assertContains(self.client.get(self.url), 'Управление магазином')

    def test_price_name_hide_reset_for_base_avatar(self):
        from . import avatars
        self.client.login(username='sa', password='p')
        self.post(action='save', kind='avatar', key='av21', price='50', ru='Дракончик', kk='Айдаһар', hidden='')
        a = avatars.by_key()['av21']
        self.assertEqual((a['price'], a['ru']), (50, 'Дракончик'))
        self.client.login(username='ss', password='p')
        self.assertContains(self.client.get(reverse('shop')), 'Дракончик')
        self.client.post(reverse('shop_buy'), {'avatar': 'av21'})
        from .coins import balance
        self.assertEqual(balance(Student.objects.get(id=self.st.id)), 4950)             # куплен по новой цене
        self.client.login(username='sa', password='p')
        self.post(action='save', kind='avatar', key='av22', price='200', ru='Панда', kk='Панда', hidden='on')
        self.assertTrue(avatars.by_key()['av22']['hidden'])
        self.client.login(username='ss', password='p')
        self.assertNotContains(self.client.get(reverse('shop')), 'value="av22"')         # скрытый не продаётся
        self.client.post(reverse('shop_buy'), {'avatar': 'av22'})
        self.assertNotIn('av22', avatars.owned(Student.objects.get(id=self.st.id)))
        self.client.login(username='sa', password='p')
        self.post(action='reset', kind='avatar', key='av21')
        self.assertEqual(avatars.by_key()['av21']['price'], 500)

    def test_banner_price_and_hide(self):
        from . import banners
        self.client.login(username='sa', password='p')
        self.post(action='save', kind='banner', key='neon', price='123', ru='Неон', kk='Неон', hidden='on')
        b = banners.catalog()['neon']
        self.assertEqual((b['price'], b['hidden']), (123, True))
        self.client.login(username='ss', password='p')
        self.client.post(reverse('shop_buy'), {'banner': 'neon'})
        self.assertNotIn('neon', banners.owned(Student.objects.get(id=self.st.id)))

    def test_add_and_delete_own_avatar(self):
        from . import avatars
        from .models import ShopItem
        self.client.login(username='sa', password='p')
        self.post(action='add_avatar', ru='Новый', kk='Жаңа', price='700', photo=self.png())
        it = ShopItem.objects.get(ru='Новый')
        self.assertEqual(it.key, f'x{it.id}')
        self.assertEqual(avatars.by_key()[it.key]['price'], 700)
        self.client.login(username='ss', password='p')
        img = self.client.get(avatars.img_url(avatars.by_key()[it.key]))
        self.assertEqual((img.status_code, img['Content-Type']), (200, 'image/webp'))
        self.client.post(reverse('shop_buy'), {'avatar': it.key})
        st = Student.objects.get(id=self.st.id)
        self.assertEqual((st.avatar, avatars.get(st.avatar)['ru']), (it.key, 'Новый'))
        self.assertContains(self.client.get(reverse('profile')), avatars.img_url(avatars.by_key()[it.key]))
        self.client.login(username='sa', password='p')
        self.post(action='delete', kind='avatar', key=it.key)
        self.assertIsNone(avatars.get(it.key))

    def test_own_avatar_price(self):
        from . import avatars
        self.client.login(username='sa', password='p')
        self.post(action='save', kind='avatar', key='own', price='900')
        self.assertEqual(avatars.own_price(), 900)
        self.post(action='save', kind='avatar', key='own', price=str(avatars.OWN_PRICE))
        self.assertEqual(avatars.own_price(), avatars.OWN_PRICE)


class ExamReportTests(RetakeTestBase):
    """Анализ СОР/СОЧ в Word: классы + четверть → .docx (несколько классов — .zip)."""

    def setUp(self):
        super().setUp()
        Exam.objects.filter(id__in=[self.exam.id, self.random_exam.id]).update(quarter=2)
        self.attempt(self.exam, self.students[0], 10)
        self.attempt(self.exam, self.students[1], 3)
        self.attempt(self.random_exam, self.students[2], 7, task_ids=[self.a_tasks[1].id, self.b_tasks[1].id])
        self.url = reverse('exam_report')

    def text(self, data):
        import docx, io
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(' | '.join(c.text for c in row.cells))
        return '\n'.join(parts)

    def test_page_and_access(self):
        self.client.force_login(self.students[0].user)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.teacher_user)
        r = self.client.get(self.url)
        self.assertContains(r, 'Анализ СОР/СОЧ')
        self.assertContains(r, 'value="7F"')
        self.assertContains(self.client.get(reverse('admin_dashboard')), reverse('exam_report'))

    def test_one_class_gives_docx_kk(self):
        self.client.force_login(self.teacher_user)
        r = self.client.post(self.url, {'classes': ['7F'], 'quarter': '2', 'kinds': ['SOR', 'SOCH'], 'lang': 'kk'})
        self.assertEqual(r['Content-Type'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.assertIn('.docx', r['Content-Disposition'])
        txt = self.text(r.content)
        self.assertIn('ЕСЕП', txt)
        self.assertIn('бөлім бойынша жиынтық бағалау (БЖБ)', txt)
        self.assertIn('тоқсан бойынша жиынтық бағалау (ТЖБ)', txt)     # СОЧ — отдельный отчёт в том же файле
        self.assertIn('Сынып: 7F', txt)
        self.assertIn('Тоқсан: 2', txt)
        self.assertIn('БЖБ бойынша орташа балл: 6,5', txt)               # (10 + 3) / 2
        self.assertIn('Ең жоғары балл: 10', txt)
        self.assertIn('Жоғары (8,5-10) | 1', txt)
        self.assertIn('Төмен (6,5-ке дейін) | 1', txt)
        self.assertIn('Ученик 1', txt)                                   # низкий — в рекомендациях
        self.assertIn('Жұмысты жазбағандар', txt)

    def test_russian_and_only_sor(self):
        self.client.force_login(self.teacher_user)
        r = self.client.post(self.url, {'classes': ['7F'], 'quarter': '2', 'kinds': ['SOR'], 'lang': 'ru'})
        txt = self.text(r.content)
        self.assertIn('ОТЧЁТ', txt)
        self.assertIn('Средний балл за СОР: 6,5', txt)
        self.assertNotIn('СОЧ)', txt)

    def test_several_classes_zip_and_empty_quarter(self):
        import io, zipfile
        Exam.objects.create(kind='SOR', title='СОР 8A', school_class='8A', quarter=2,
                            start_at=timezone.now() - timedelta(hours=2), duration_minutes=40, created_by=self.teacher)
        self.client.force_login(User.objects.create_superuser('rep_admin', password='x'))
        r = self.client.post(self.url, {'classes': ['7F', '8A'], 'quarter': '2', 'kinds': ['SOR', 'SOCH'], 'lang': 'ru'})
        self.assertEqual(r['Content-Type'], 'application/zip')
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertEqual(len(names), 2)
        self.assertTrue(all(n.endswith('.docx') for n in names))
        r = self.client.post(self.url, {'classes': ['7F'], 'quarter': '4', 'kinds': ['SOR'], 'lang': 'ru'})
        self.assertRedirects(r, self.url)                                # в 4 четверти работ нет

    def test_teacher_cannot_get_foreign_class(self):
        self.client.force_login(self.teacher_user)
        classes = self.client.get(self.url).context['classes']
        if '8A' not in classes:
            r = self.client.post(self.url, {'classes': ['8A'], 'quarter': '2', 'kinds': ['SOR'], 'lang': 'ru'})
            self.assertRedirects(r, self.url)


class EmpireGameTests(TestCase):
    """«Империя»: шестиугольники, ханы, столица, города (+6 клеток), союзы и перемирие."""

    def setUp(self):
        cache.clear()
        from .models import IslandGame
        self.tu = User.objects.create_user('em_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='7A')
        self.sts = [Student.objects.create(user=User.objects.create_user(f'em_s{i}', password='p'),
                                           full_name=f'Ученик Номер{i}', school_class='7A') for i in range(6)]
        tag = Tag.objects.create(name='империя')
        make_task('Код A', 'A', tag)
        make_task('Код C', 'C', tag)
        self.game = IslandGame(owner=self.tu, title='Империя', mode='teams', teams_count=2, minutes=40, variant='empire')
        self.game.set_classes(['7A'])
        self.game.save()
        self.game.tags.add(tag)

    def _run(self, preset=None):
        from . import island
        import random
        for st in self.sts:
            island.join(self.game, st)
        if preset:
            self.game.empire = {'names': preset}
            self.game.save(update_fields=['empire'])
        island.shuffle_teams(self.game, random.Random(1))
        self.game = island.start(self.game, random.Random(2))
        return island

    def _capitals(self):
        """Ханы всех команд строят столицы там, где стоят (без столицы команда не ходит)."""
        from . import empire
        for t in list(self.game.empire['teams']):
            k = self._khan(int(t))
            self.assertEqual(empire.build_capital(self.game, k)['ok'], True)
        self.game.refresh_from_db()

    def _set(self, x, y, kind):
        rows = [list(r) for r in self.game.map['rows']]
        rows[y][x] = kind
        self.game.map['rows'] = [''.join(r) for r in rows]
        self.game.save(update_fields=['map'])

    def _own(self, x, y, team):
        from .models import IslandCell
        IslandCell.objects.update_or_create(game=self.game, x=x, y=y, defaults={'team': team, 'ver': 1})

    def _khan(self, team):
        from .models import IslandPlayer
        self.game.refresh_from_db()
        return IslandPlayer.objects.get(pk=self.game.empire['teams'][str(team)]['khan'])

    def _free_neighbour(self, island, p):
        """Ничья клетка рядом с игроком; если рядом нет — ставим игрока на свою клетку у края земли."""
        owned = {(c.x, c.y): c.team for c in self.game.cells.all()}
        inside = lambda n: 0 < n[0] < self.game.map['w'] - 1 and 0 < n[1] < self.game.map['h'] - 1
        spots = [(p.x, p.y)] + [c for c, t in owned.items() if t == p.team]
        for spot in spots:
            for n in island.neighbors(self.game, *spot):
                if n not in owned and inside(n):
                    if spot != (p.x, p.y):
                        p.x, p.y = spot
                        p.save(update_fields=['x', 'y'])
                    return n
        return None

    def test_start_gives_hex_map_khans_and_seven_cells(self):
        from . import empire
        island = self._run(preset={'1': 'kazakh'})
        self.assertTrue(self.game.map.get('hex'))
        for t in (0, 1):
            self.assertEqual(self.game.cells.filter(team=t).count(), empire.START_CELLS)
            khan = self._khan(t)
            self.assertEqual(khan.team, t)
        names = self.game.empire['names']
        self.assertEqual(names['1'], 'kazakh')
        self.assertNotEqual(names['0'], names['1'])
        self.assertEqual(self.game.scores, {'0': 7, '1': 7})
        st = island.state(self.game, lang='ru')
        self.assertEqual(st['variant'], 'empire')
        self.assertEqual(st['empire']['teams']['1']['name'], 'Казахское ханство')
        self.assertIn('Казахское ханство', [t['name'] for t in st['teams']])

    def test_walk_capture_and_border_rule(self):
        from . import empire
        island = self._run()
        p = self.game.players.filter(team=0).first()
        land = [n for n in island.neighbors(self.game, p.x, p.y) if island.terrain(self.game, *n) != island.WATER]
        self.assertEqual(island.move(self.game, p, *land[0]), {'error': 'wait_capital'})
        self._capitals()
        p.refresh_from_db()
        n = self._free_neighbour(island, p)
        self._set(*n, '.')
        res = island.move(self.game, p, *n)
        self.assertTrue(res.get('challenge'))
        p.refresh_from_db()
        self.assertEqual(p.challenge['kind'], 'output')
        self.assertEqual(island.answer(self.game, p, p.challenge['answer'])['ok'], True)
        self.game.refresh_from_db()
        self.assertEqual(self.game.scores['0'], 8 + empire.CAPITAL_PTS)
        # вражеская клетка рядом: со своей земли — можно (задача сложнее), с ничьей — нельзя
        p.refresh_from_db()
        m = self._free_neighbour(island, p)
        self._set(*m, '.')
        self._own(*m, 1)
        self.assertEqual(empire.move_rule(self.game, p, m[0], m[1], 1), {'error': 'neutral_err'})   # нейтральных не трогают
        self.game.empire['rel'] = {'0-1': {'s': 'enemy', 'lock': None}}
        self.game.save(update_fields=['empire'])
        rule = empire.move_rule(self.game, p, m[0], m[1], 1)
        self.assertEqual((rule['level'], rule['enemy']), (2, True))
        self.game.cells.filter(x=p.x, y=p.y).delete()                         # стоит не на своей клетке
        self.assertEqual(empire.move_rule(self.game, p, m[0], m[1], 1), {'error': 'border'})

    def test_alliance_walk_break_and_lock(self):
        from datetime import timedelta
        from . import empire
        island = self._run()
        self._capitals()
        k0, k1 = self._khan(0), self._khan(1)
        self.assertEqual(empire.relation(self.game, 0, 1), 'neutral')                 # в начале все нейтральны
        sarbaz = self.game.players.filter(team=0).exclude(pk=k0.pk).first()
        self.assertEqual(empire.diplomacy(self.game, sarbaz, 'propose', 1), {'error': 'notkhan'})
        self.assertEqual(empire.diplomacy(self.game, k0, 'propose', 1), {'ok': True})
        self.game.refresh_from_db()
        self.assertEqual(self.game.empire['offers'], [[0, 1, 'ally']])
        self.assertEqual(empire.diplomacy(self.game, k1, 'accept', 0), {'ok': True})
        self.game.refresh_from_db()
        self.assertEqual(empire.relation(self.game, 0, 1), 'ally')
        p = self.game.players.filter(team=0).first()
        n = self._free_neighbour(island, p)
        self._set(*n, '.')
        self._own(*n, 1)
        self.assertEqual(empire.move_rule(self.game, p, n[0], n[1], 1), {'walk': True})    # по земле союзника — свободно
        self.assertEqual(island.move(self.game, p, *n), {'moved': True})
        # 5 минут союз не разорвать
        self.assertEqual(empire.diplomacy(self.game, k1, 'break', 0)['error'], 'locked')
        later = timezone.now() + timedelta(seconds=empire.LOCK + 1)
        self.assertEqual(empire.diplomacy(self.game, k1, 'break', 0, now=later), {'ok': True})
        self.game.refresh_from_db()
        self.assertEqual(empire.relation(self.game, 0, 1), 'neutral')                 # разрыв — нейтралитет, не война
        q = self.game.players.filter(team=0).exclude(pk=p.pk).first()
        self.assertEqual(empire.move_rule(self.game, q, n[0], n[1], 1), {'error': 'neutral_err'})
        # стоявший на земле бывшего союзника — перенесён на свою землю
        p.refresh_from_db()
        self.assertNotEqual((p.x, p.y), tuple(n))
        self.assertTrue(self.game.cells.filter(x=p.x, y=p.y, team=0).exists())

    def test_war_by_khan_alone_and_peace_by_agreement(self):
        from datetime import timedelta
        from . import empire
        self._run()
        self._capitals()
        k0, k1 = self._khan(0), self._khan(1)
        self.assertEqual(empire.diplomacy(self.game, k0, 'war', 1), {'ok': True})       # войну хан объявляет сам
        self.game.refresh_from_db()
        self.assertEqual(empire.relation(self.game, 0, 1), 'enemy')
        self.assertGreater(empire.lock_left(self.game, 0, 1), 290)
        self.assertEqual(empire.diplomacy(self.game, k1, 'propose', 0, kind='peace')['error'], 'locked')
        later = timezone.now() + timedelta(seconds=empire.LOCK + 1)
        self.assertEqual(empire.diplomacy(self.game, k1, 'propose', 0, now=later, kind='peace'), {'ok': True})
        self.game.refresh_from_db()
        self.assertEqual(empire.relation(self.game, 0, 1), 'enemy')                   # пока второй хан не согласился
        self.assertEqual(empire.diplomacy(self.game, k0, 'accept', 1, now=later, kind='peace'), {'ok': True})
        self.game.refresh_from_db()
        self.assertEqual(empire.relation(self.game, 0, 1), 'neutral')
        self.assertEqual(self.game.empire['offers'], [])

    def test_city_takes_seven_cells_but_not_allied(self):
        from . import empire
        island = self._run()
        self._capitals()
        p = self.game.players.filter(team=0).first()
        n = self._free_neighbour(island, p)
        self._set(*n, empire.CITY)
        around = [c for c in empire.hex_neighbors(*n) if island.terrain(self.game, *c) != island.WATER]
        for c in around:
            self._set(*c, '.')
        enemy_cell, ally_cell = around[-1], around[-2]
        if (p.x, p.y) in (enemy_cell, ally_cell):
            enemy_cell, ally_cell = around[0], around[1]
        self._own(*enemy_cell, 1)
        rule = empire.move_rule(self.game, p, n[0], n[1], None)
        self.assertEqual((rule['level'], rule['points'], rule['city']), (empire.CITY_LEVEL, 5, True))
        self.game.empire['rel'] = {'0-1': {'s': 'ally', 'lock': None}, '0-2': {'s': 'enemy', 'lock': None}}
        self.game.save(update_fields=['empire'])
        self._own(*ally_cell, 1)
        self._own(*enemy_cell, 2)                                            # «чужая» команда 2 — враг
        self.game.empire['teams']['2'] = {'name': 'saka', 'khan': None, 'changes': 0, 'capital': None, 'built': False}
        self.game.save(update_fields=['empire'])
        empire.capture(self.game, p, dict(rule, x=n[0], y=n[1]))
        self.game.refresh_from_db()
        owner = {(c.x, c.y): c.team for c in self.game.cells.all()}
        self.assertEqual(owner[n], 0)
        self.assertEqual(owner[enemy_cell], 0)                               # враг потерял клетку
        self.assertEqual(owner[ally_cell], 1)                                # союзника не трогаем
        self.assertEqual(self.game.scores['0'], sum(5 if c == n else 1 for c, t in owner.items() if t == 0) + empire.CAPITAL_PTS)

    def test_capital_first_then_move_only_to_city(self):
        from . import empire
        island = self._run()
        k0 = self._khan(0)
        sarbaz = self.game.players.filter(team=0).exclude(pk=k0.pk).first()
        self.assertEqual(empire.build_capital(self.game, sarbaz), {'error': 'notkhan'})
        foreign = self.game.cells.filter(team=1).first()
        self.assertEqual(empire.build_capital(self.game, k0, foreign.x, foreign.y), {'error': 'not_own'})
        spot = self.game.cells.filter(team=0).exclude(x=k0.x, y=k0.y).first()
        self.assertEqual(empire.build_capital(self.game, k0, spot.x, spot.y), {'ok': True, 'first': True})
        self.game.refresh_from_db()
        self.assertEqual(self.game.scores['0'], 7 + empire.CAPITAL_PTS)
        for p in self.game.players.filter(team=0):                            # вся команда встала в столицу
            self.assertEqual((p.x, p.y), (spot.x, spot.y))
        other = self.game.cells.filter(team=0).exclude(x=spot.x, y=spot.y).first()
        self.assertEqual(empire.build_capital(self.game, k0, other.x, other.y), {'error': 'need_city'})
        self._set(other.x, other.y, empire.CITY)
        self.assertEqual(empire.build_capital(self.game, k0, other.x, other.y), {'ok': True, 'first': False})
        self.game.refresh_from_db()
        self.assertEqual(self.game.empire['teams']['0']['capital'], [other.x, other.y])

    def test_capturing_capital_takes_whole_empire_and_victim_restarts(self):
        from . import empire
        island = self._run()
        self._capitals()
        cap = tuple(self.game.empire['teams']['0']['capital'])
        before0 = {(c.x, c.y) for c in self.game.cells.filter(team=0)}
        self.assertEqual(len(before0), 7)
        enemy = self.game.players.filter(team=1).first()
        empire.capture(self.game, enemy, {'x': cap[0], 'y': cap[1], 'city': True, 'points': 5})
        self.game.refresh_from_db()
        owner = {(c.x, c.y): c.team for c in self.game.cells.all()}
        self.assertTrue(all(owner[c] == 1 for c in before0))                  # вся бывшая земля — захватчику
        new0 = [c for c, t in owner.items() if t == 0]
        self.assertEqual(len(new0), empire.START_CELLS)                       # и сразу 7 новых клеток
        self.assertFalse(set(new0) & before0)
        self.assertIsNone(self.game.empire['teams']['0']['capital'])
        self.assertEqual(self.game.empire['teams']['0']['restarts'], 1)
        for p in self.game.players.filter(team=0):
            self.assertIn((p.x, p.y), new0)
            self.assertEqual(empire.move_rule(self.game, p, p.x, p.y, 0), {'error': 'wait_capital'})
        k0 = self._khan(0)
        self.assertEqual(empire.build_capital(self.game, k0, *new0[0])['ok'], True)   # снова любая своя клетка

    def test_restart_takes_cells_from_captor_when_no_free_land(self):
        from . import empire
        from .models import IslandCell
        island = self._run()
        self._capitals()
        # вся ничья суша — у команды 1: свободных клеток нет
        for y, row in enumerate(self.game.map['rows']):
            for x, ch in enumerate(row):
                if ch != island.WATER and not IslandCell.objects.filter(game=self.game, x=x, y=y).exists():
                    IslandCell.objects.create(game=self.game, x=x, y=y, team=1, ver=1)
        cap = tuple(self.game.empire['teams']['0']['capital'])
        enemy = self.game.players.filter(team=1).first()
        empire.capture(self.game, enemy, {'x': cap[0], 'y': cap[1], 'city': True, 'points': 5})
        self.game.refresh_from_db()
        mine = self.game.cells.filter(team=0)
        self.assertEqual(mine.count(), empire.START_CELLS)
        cap1 = tuple(self.game.empire['teams']['1']['capital'])
        self.assertFalse(mine.filter(x=cap1[0], y=cap1[1]).exists())          # столицу захватчика не отдают

    def test_khan_changes_limited_and_kicked_khan_passes_crown(self):
        from . import empire
        island = self._run()
        k = self._khan(0)
        mates = list(self.game.players.filter(team=0).exclude(pk=k.pk))
        foreign = self.game.players.filter(team=1).first()
        self.assertEqual(empire.appoint(self.game, k, foreign.id), {'error': 'bad_target'})
        cur = k
        for i in range(empire.MAX_KHAN_CHANGES):
            nxt = mates[i % len(mates)] if mates[i % len(mates)].pk != cur.pk else k
            self.assertEqual(empire.appoint(self.game, cur, nxt.id), {'ok': True})
            cur = self._khan(0)
        other = next(p for p in self.game.players.filter(team=0) if p.pk != cur.pk)
        self.assertEqual(empire.appoint(self.game, cur, other.id), {'error': 'no_changes'})
        for _ in range(island.MAX_EXITS):                                    # хана выбило — корона переходит сама
            island.count_exit(cur, 'blur')
        new = self._khan(0)
        self.assertNotEqual(new.pk, cur.pk)
        self.assertEqual(new.team, 0)
        self.assertEqual(self.game.empire['teams']['0']['changes'], empire.MAX_KHAN_CHANGES)

    def test_views_create_state_and_khan_endpoint(self):
        from .models import IslandGame
        self.client.force_login(self.tu)
        r = self.client.post(reverse('island_manage'), {'classes': ['7A'], 'mode': 'solo', 'variant': 'empire', 'teams_count': 3, 'minutes': 30})
        g = IslandGame.objects.exclude(pk=self.game.pk).get()
        self.assertEqual((g.variant, g.mode), ('empire', 'teams'))                    # «Империя» — только команды
        self.assertContains(self.client.get(reverse('island_control', args=[g.id])), 'случайная империя')
        self.client.post(reverse('island_control_action', args=[g.id]), {'action': 'empire_name', 'team': 0, 'name': 'golden'})
        g.refresh_from_db()
        self.assertEqual(g.empire['names'], {'0': 'golden'})
        island = self._run()
        p = self.game.players.first()
        self.client.force_login(p.student.user)
        self.assertContains(self.client.get(reverse('island_play', args=[self.game.id])), 'Империя')
        self.assertEqual(self.client.post(reverse('island_khan', args=[self.game.id]), {'action': 'capital'}).json(), {'error': 'notarmed'})
        self.client.post(reverse('island_arm', args=[self.game.id]))
        r = self.client.post(reverse('island_khan', args=[self.game.id]), {'action': 'propose', 'team': 1 - p.team}).json()
        khan = self._khan(p.team)
        self.assertEqual(r, {'ok': True} if khan.pk == p.pk else {'error': 'notkhan'})
        st = self.client.get(reverse('island_state', args=[self.game.id])).json()
        self.assertIn('empire', st)
        self.assertTrue(st['map']['hex'])


class ScratchTrainerTests(TestCase):
    """Scratch-тренажёр: только 1–4 классы, задачи на Python у них скрыты; проверка решений на сервере, звёзды, монеты."""

    def setUp(self):
        self.junior = Student.objects.create(user=User.objects.create_user('sc_j', password='p'), full_name='Малыш', school_class='2A')
        self.senior = Student.objects.create(user=User.objects.create_user('sc_s', password='p'), full_name='Старший', school_class='7A')
        self.tu = User.objects.create_user('sc_t', password='p', is_staff=True)
        Teacher.objects.create(user=self.tu, full_name='Уч', classes='2A,7A')

    def post(self, key, program):
        import json
        return self.client.post(reverse('scratch_check', args=[key]), data=json.dumps({'program': program}), content_type='application/json').json()

    def test_every_level_is_solvable_by_its_solution(self):
        from . import scratch_levels as sl
        self.assertGreaterEqual(len(sl.LEVELS), 30)
        for lv in sl.LEVELS:
            res = sl.check(lv, sl.clean(lv['solution'], set(lv['blocks'])))
            self.assertTrue(res['ok'], lv['key'])
            self.assertEqual(res['stars'], 3, lv['key'])
            if lv['kind'] == 'maze':
                self.assertEqual(len({len(r) for r in lv['grid']}), 1, lv['key'])

    def test_is_junior(self):
        from .scratch_levels import is_junior
        for cls, want in (('1A', True), ('4Б', True), ('5A', False), ('10A', False), ('', False), ('Подготовка', False)):
            self.assertEqual(is_junior(Student(school_class=cls)), want, cls)

    def test_junior_sees_scratch_not_python(self):
        self.client.login(username='sc_j', password='p')
        self.assertRedirects(self.client.get(reverse('all_tasks')), reverse('scratch_home'))
        self.assertRedirects(self.client.get(reverse('courses')), reverse('scratch_home'))
        self.assertRedirects(self.client.get(reverse('leaderboard')), reverse('scratch_home'))
        r = self.client.get(reverse('scratch_home'))
        self.assertContains(r, '🐱 Scratch')
        self.assertNotContains(r, 'Все задачи')
        self.assertContains(r, reverse('scratch_level', args=['m1']))

    def test_senior_does_not_see_scratch(self):
        self.client.login(username='sc_s', password='p')
        self.assertRedirects(self.client.get(reverse('scratch_home')), reverse('all_tasks'), fetch_redirect_response=False)
        r = self.client.get(reverse('all_tasks'))
        self.assertNotContains(r, '🐱 Scratch')
        self.assertEqual(self.client.post(reverse('scratch_check', args=['m1']), data='{}', content_type='application/json').status_code, 403)

    def test_check_saves_stars_coins_and_unlocks_next(self):
        from .models import ScratchProgress, CoinTransaction
        from . import coins
        self.client.login(username='sc_j', password='p')
        self.assertRedirects(self.client.get(reverse('scratch_level', args=['m2'])), reverse('scratch_home'))   # закрыт
        bad = self.post('m1', [{'op': 'right'}])
        self.assertEqual((bad['ok'], bad['reason']), (False, 'short'))
        long = self.post('m1', [{'op': 'right'}, {'op': 'right'}, {'op': 'left'}])
        self.assertEqual(long['ok'], True)                                   # дошёл до рыбки раньше, чем кончились блоки
        self.assertEqual(long['stars'], 2)
        ok = self.post('m1', [{'op': 'right'}, {'op': 'right'}])
        self.assertEqual((ok['ok'], ok['stars'], ok['next']), (True, 3, 'm2'))
        p = ScratchProgress.objects.get(student=self.junior, level='m1')
        self.assertEqual((p.stars, p.blocks, p.attempts), (3, 2, 3))
        self.assertEqual(coins.balance(self.junior), 3)                       # 2 за прохождение + 1 за три звезды
        self.post('m1', [{'op': 'right'}, {'op': 'right'}])
        self.assertEqual(coins.balance(self.junior), 3)                       # повторно не начисляется
        self.assertEqual(self.client.get(reverse('scratch_level', args=['m2'])).status_code, 200)
        self.assertEqual(self.post('m1', [{'op': 'fwd'}]), {'error': 'bad_program'})     # блок не из этого уровня

    def test_pen_drawing_compared_with_sample(self):
        self.client.login(username='sc_j', password='p')
        sq = [{'op': 'repeat', 'n': 4, 'body': [{'op': 'move', 'n': 3}, {'op': 'turn', 'dir': 'r', 'deg': 90}]}]
        for k in ('p1', 'p2', 'p3'):                                          # открываем по очереди
            from . import scratch_levels as sl
            self.assertTrue(self.post(k, sl.BY_KEY[k]['solution'])['ok'])
        self.assertEqual(self.post('p4', sq)['stars'], 3)
        wrong = [{'op': 'repeat', 'n': 4, 'body': [{'op': 'move', 'n': 2}, {'op': 'turn', 'dir': 'r', 'deg': 90}]}]
        self.assertEqual(self.post('p4', wrong)['ok'], False)
        other_way = [{'op': 'move', 'n': 3}, {'op': 'turn', 'dir': 'r', 'deg': 90}, {'op': 'move', 'n': 3}, {'op': 'turn', 'dir': 'r', 'deg': 90},
                     {'op': 'move', 'n': 3}, {'op': 'turn', 'dir': 'r', 'deg': 90}, {'op': 'move', 'n': 1}, {'op': 'move', 'n': 2}]
        res = self.post('p4', other_way)
        self.assertEqual((res['ok'], res['stars']), (True, 1))                # тот же квадрат, но длинно — 1 звезда

    def test_teacher_preview_and_progress_page(self):
        self.client.login(username='sc_t', password='p')
        r = self.client.get(reverse('scratch_home'))
        self.assertContains(r, 'все уровни открыты')
        self.assertEqual(self.client.get(reverse('scratch_level', args=['p14'])).status_code, 200)
        from . import scratch_levels as sl
        self.assertTrue(self.post('m21', sl.BY_KEY['m21']['solution'])['ok'])
        from .models import ScratchProgress
        self.assertFalse(ScratchProgress.objects.exists())                    # у учителя ничего не сохраняется
        r = self.client.get(reverse('scratch_teacher'))
        self.assertContains(r, 'Малыш')
        self.assertNotContains(r, 'Старший')
        self.assertContains(self.client.get(reverse('teacher_dashboard')), reverse('scratch_teacher'))


class TeacherMyClassesTests(TestCase):
    """Учитель сам меняет свои классы."""

    def setUp(self):
        self.tu = User.objects.create_user('mc_t', password='p', is_staff=True)
        self.t = Teacher.objects.create(user=self.tu, full_name='Уч', classes='7A')
        Student.objects.create(user=User.objects.create_user('mc_s', password='p'), full_name='Ученик', school_class='7B')

    def test_teacher_changes_own_classes(self):
        self.client.login(username='mc_t', password='p')
        self.assertContains(self.client.get(reverse('teacher_dashboard')), reverse('teacher_my_classes'))
        r = self.client.get(reverse('teacher_my_classes'))
        self.assertContains(r, 'value="7A" checked')
        self.assertContains(r, 'value="7B"')
        r = self.client.post(reverse('teacher_my_classes'), {'classes': ['7B'], 'extra': '2 а, 3b'})
        self.assertRedirects(r, reverse('teacher_dashboard'), fetch_redirect_response=False)
        self.t.refresh_from_db()
        self.assertEqual(self.t.class_list(), ['7B', '2A', '3B'])
        from .views import _managed_classes
        self.assertEqual(_managed_classes(self.tu), ['7B', '2A', '3B'])

    def test_students_cannot_open(self):
        self.client.login(username='mc_s', password='p')
        self.assertNotEqual(self.client.get(reverse('teacher_my_classes')).status_code, 200)


class AiSignalsTests(TestCase):
    """Признаки ИИ: приёмы не по уровню, новые привычки, одинаковые решения у разных учеников."""
    AI = ('import sys\n\ndef main() -> None:\n    """Read two numbers and print their sum."""\n    # Read input values\n'
          '    a, b = map(int, sys.stdin.readline().split())\n    # Print the result\n    print(f"{a + b}")\n\n'
          'if __name__ == "__main__":\n    main()\n')
    KID = 'a = int(input())\nb = int(input())\nprint(a + b)\n'
    LOOP = ('n = int(input())\ns = 0\nfor i in range(n):\n    x = int(input())\n    if x % 2 == 0:\n        s = s + x\n'
            '    else:\n        s = s - x\nprint(s)\nif s > 10:\n    print("big")\nelse:\n    print("small")\n')

    def test_features(self):
        from . import ai_signals as a
        keys = {k for k, _ in a.advanced_for(self.AI, '7A')}
        self.assertTrue({'main_guard', 'annotations', 'docstring', 'english_comments'} <= keys)
        self.assertEqual(a.advanced_for(self.KID, '7A'), [])
        typo = 'ping = int(input())\nif ping < 50:\n    print("perfect")\nelse:\n    print:("lag")\n'
        self.assertEqual(a.advanced_for(typo, '6N'), [])                               # опечатка print:(...) — не аннотация
        self.assertIn('annotations', a.features('x: int = 5\n'))
        self.assertIn('annotations', a.features('def f(a: list[int]) -> None:\n    pass\n'))
        self.assertEqual(a.advanced_for('print([x for x in range(3)])', '7A'), [])      # списковые включения — норма
        comp = 'a = [int(x) for x in input().split()]\nprint(sum(a))\n'
        self.assertEqual(a.new_habits('print(sum([int(x) for x in input().split()]))\n# сумма\n# ответ\n', [comp] * 5),
                         [('comments', 'комментарии')])                                   # кто уже писал так — не новость
        self.assertEqual(a.new_habits(self.AI, [self.KID] * 3), [])                    # мало истории — не судим
        self.assertGreaterEqual(len(a.new_habits(self.AI, [self.KID] * 5)), 2)

    def test_suspicious_page_and_grade_page(self):
        from .models import Submission
        tu = User.objects.create_user('ai_t', password='p', is_staff=True)
        Teacher.objects.create(user=tu, full_name='Уч', classes='7A')
        s1 = Student.objects.create(user=User.objects.create_user('ai_1', password='p'), full_name='Первый', school_class='7A')
        s2 = Student.objects.create(user=User.objects.create_user('ai_2', password='p'), full_name='Второй', school_class='7A')
        tag = Tag.objects.create(name='ии')
        task = make_task('Сумма чётных', 'A', tag)
        sub = Submission.objects.create(student=s1, task=task, code=self.LOOP, score=10)
        Submission.objects.create(student=s2, task=task, code=self.LOOP, score=10)       # одинаковый код — не сравниваем
        own = Submission.objects.create(student=s1, task=make_task('Сумма', 'A', tag), code=self.AI, score=10)
        self.client.login(username='ai_t', password='p')
        r = self.client.get(reverse('suspicious_students'))
        self.assertContains(r, 'Возможен ИИ')
        self.assertNotContains(r, 'Почти такой же')
        self.assertNotContains(r, 'Второй')
        r = self.client.get(reverse('grade_submission', args=[own.id]))
        self.assertContains(r, 'Признаки ИИ')
        self.assertContains(r, 'аннотации типов')
        self.assertContains(r, '>Код скопирован из ИИ</textarea>')                      # обратная связь подставлена
        r = self.client.get(reverse('grade_submission', args=[sub.id]))
        self.assertNotContains(r, 'Признаки ИИ')
        self.assertNotContains(r, '>Код скопирован из ИИ</textarea>')
        self.assertContains(r, 'Код из ИИ → 0')                                          # быстрая кнопка есть всегда


class Gta6ChallengeTests(TestCase):
    """Челлендж GTA VI: 10 задач (7 на ввод/вывод и if/else), за 6 на 10/10 — баннер «Vice City» и ОДИН аватар на выбор."""

    def setUp(self):
        self.st = Student.objects.create(user=User.objects.create_user('gta_s', password='p'), full_name='Игрок', school_class='8A')
        self.client.login(username='gta_s', password='p')

    def solve(self, task, score=10):
        from .models import Submission
        Submission.objects.update_or_create(student=self.st, task=task, defaults={'score': score, 'is_graded': True, 'status': 'DONE', 'code': 'x'})

    def test_tasks_loaded(self):
        tasks = Task.objects.filter(tags__name='GTA VI')
        self.assertEqual(tasks.count(), 10)
        self.assertEqual(sorted(t.level for t in tasks), ['A'] * 5 + ['B'] * 2 + ['C'] * 3)
        self.assertFalse(tasks.filter(title__in=['Радио в машине', 'Небоскрёбы', 'Сувениры']).exists())
        for t in tasks:
            self.assertTrue(t.title_kk and t.description_kk)
            self.assertGreaterEqual(len(t.test_cases), 5)

    def test_six_solved_gives_banner_then_one_avatar(self):
        from . import avatars, banners
        tasks = list(Task.objects.filter(tags__name='GTA VI').order_by('id'))
        r = self.client.get(reverse('challenge', args=['gta6']))
        self.assertContains(r, 'Челлендж GTA VI')
        self.assertContains(r, '0 / 6')
        self.assertNotContains(r, reverse('challenge_pick', args=['gta6']))        # выбирать ещё нельзя
        self.client.post(reverse('challenge_pick', args=['gta6']), {'avatar': 'gta01'})
        self.st.refresh_from_db()
        self.assertFalse(avatars.owned(self.st))
        for t in tasks[:5]:
            self.solve(t)
        self.solve(tasks[5], score=7)
        self.client.get(reverse('challenge', args=['gta6']))
        self.st.refresh_from_db()
        self.assertNotIn('vice', banners.owned(self.st))
        self.solve(tasks[5])
        r = self.client.get(reverse('challenge', args=['gta6']))
        self.assertContains(r, 'Поздравляем')
        self.assertContains(r, reverse('challenge_pick', args=['gta6']))
        self.st.refresh_from_db()
        self.assertIn('vice', banners.owned(self.st))
        self.assertEqual(self.st.name_banner, 'vice')
        self.client.post(reverse('challenge_pick', args=['gta6']), {'avatar': 'gta03'})
        self.client.post(reverse('challenge_pick', args=['gta6']), {'avatar': 'gta05'})   # второй — нельзя
        self.st.refresh_from_db()
        self.assertEqual({k for k in avatars.owned(self.st) if k.startswith('gta')}, {'gta03'})
        self.assertEqual(self.st.avatar, 'gta03')
        self.client.post(reverse('challenge_pick', args=['gta6']), {'avatar': 'av01'})    # чужой ключ — нельзя
        self.st.refresh_from_db()
        self.assertNotIn('av01', avatars.owned(self.st))

    def test_deadline_timer_and_closed_after_end(self):
        from datetime import timedelta
        from unittest import mock
        from django.utils import timezone
        from . import avatars, banners
        ch = banners.CHALLENGES['gta6']
        end = banners.ends_at(ch)
        self.assertEqual(timezone.localtime(end).strftime('%d.%m %H:%M'), '24.11 23:59')
        r = self.client.get(reverse('challenge', args=['gta6']))
        self.assertContains(r, 'data-countdown="' + end.isoformat())
        r = self.client.get(reverse('shop'))                          # полоса с таймером на других страницах
        self.assertContains(r, 'Mission completed!')
        self.assertContains(r, 'data-countdown=')
        tasks = list(Task.objects.filter(tags__name='GTA VI').order_by('id'))
        for t in tasks[:6]:
            self.solve(t)
        with mock.patch('contest.banners.timezone_now', return_value=end + timedelta(minutes=1)):
            r = self.client.get(reverse('challenge', args=['gta6']))
            self.assertContains(r, 'Челлендж завершён')
            self.assertNotContains(r, reverse('challenge_pick', args=['gta6']))
            self.client.post(reverse('challenge_pick', args=['gta6']), {'avatar': 'gta02'})
            self.st.refresh_from_db()
            self.assertNotIn('vice', banners.owned(self.st))
            self.assertFalse({k for k in avatars.owned(self.st) if k.startswith('gta')})
            self.assertNotContains(self.client.get(reverse('shop')), 'Mission completed!')

    def test_earned_before_end_can_pick_after(self):
        from datetime import timedelta
        from unittest import mock
        from . import avatars, banners
        for t in list(Task.objects.filter(tags__name='GTA VI').order_by('id'))[:6]:
            self.solve(t)
        self.client.get(reverse('challenge', args=['gta6']))          # баннер выдан вовремя
        end = banners.ends_at(banners.CHALLENGES['gta6'])
        with mock.patch('contest.banners.timezone_now', return_value=end + timedelta(days=1)):
            self.client.post(reverse('challenge_pick', args=['gta6']), {'avatar': 'gta04'})
        self.st.refresh_from_db()
        self.assertIn('gta04', avatars.owned(self.st))

    def test_gift_not_for_sale_and_shop_links_to_challenge(self):
        from . import avatars, banners
        r = self.client.get(reverse('shop'))
        self.assertContains(r, reverse('challenge', args=['gta6']))
        self.client.post(reverse('shop_buy'), {'avatar': 'gta01'})
        self.client.post(reverse('shop_buy'), {'banner': 'vice'})
        self.st.refresh_from_db()
        self.assertNotIn('gta01', avatars.owned(self.st))
        self.assertNotIn('vice', banners.owned(self.st))
        self.assertEqual(self.client.get(reverse('challenge', args=['nope'])).status_code, 404)


class StudyRoomTests(TestCase):
    """3D-коворкинг класса: учитель открывает зал с задачами, ученики садятся, пишут, поднимают руку; статистика урока."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.tu = User.objects.create_user('rm_t', password='p', is_staff=True, is_superuser=True)
        self.sts = [Student.objects.create(user=User.objects.create_user(f'rm_s{i}', password='p'), full_name=f'Ученик {i}', school_class='8A')
                    for i in range(3)]
        self.other = Student.objects.create(user=User.objects.create_user('rm_o', password='p'), full_name='Чужой', school_class='8B')
        tag = Tag.objects.create(name='Циклы')
        self.tasks = [make_task(f'Задача {i}', 'A', tag) for i in range(3)]

    def _open(self):
        self.client.force_login(self.tu)
        r = self.client.post(reverse('room_manage'), {'school_class': '8A', 'title': 'Циклы', 'minutes': 45,
                                                       'tasks': [t.id for t in self.tasks[:2]]})
        room = StudyRoom.objects.get()
        self.assertRedirects(r, reverse('room_play', args=[room.id]))
        return room

    def test_open_room_with_tasks_and_students_see_it(self):
        room = self._open()
        self.assertEqual(list(room.tasks.order_by('id')), self.tasks[:2])
        self.client.force_login(self.sts[0].user)
        self.assertContains(self.client.get(reverse('all_tasks')), reverse('room_play', args=[room.id]))       # полоса-приглашение
        html = self.client.get(reverse('room_play', args=[room.id])).content.decode()
        self.assertIn('Задача 0', html)
        self.assertNotIn('Задача 2', html)
        self.client.force_login(self.other.user)
        self.assertEqual(self.client.get(reverse('room_play', args=[room.id])).status_code, 404)     # чужой класс

    def test_move_sit_say_hand_and_seat_conflict(self):
        room = self._open()
        a, b = self.sts[0], self.sts[1]
        act = reverse('room_act', args=[room.id])
        self.client.force_login(a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.assertEqual(self.client.post(act, {'a': 'move', 'x': 3, 'y': 8, 'seat': 's1'}).json(), {'ok': True})
        self.assertEqual(self.client.post(act, {'a': 'move', 'x': 10, 'y': 2, 'seat': 't'}).json()['error'], 'teacher_seat')
        self.assertEqual(self.client.post(act, {'a': 'say', 'text': 'Помогите  с задачей'}).json(), {'ok': True})
        self.assertEqual(self.client.post(act, {'a': 'say', 'text': 'ещё'}).json()['error'], 'wait')        # не чаще раза в 4 сек
        self.client.post(act, {'a': 'hand'})
        self.client.force_login(b.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.assertEqual(self.client.post(act, {'a': 'move', 'x': 3, 'y': 8, 'seat': 's1'}).json()['error'], 'taken')
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        me_a = [p for p in st['people'] if p[0] == a.user_id][0]
        self.assertEqual((me_a[4], me_a[5], me_a[6]), ('s1', 'Помогите с задачей', True))
        self.assertIn('Ученик 0', me_a[9])                                                   # плашка с именем (и баннером, если есть)
        self.assertNotIn('log', st)                                                          # журнал — только учителю
        self.client.force_login(self.tu)
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        self.assertEqual(st['log'][-1][2], 'Помогите с задачей')
        self.client.post(act, {'a': 'lower', 'uid': a.user_id})
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        self.assertFalse([p for p in st['people'] if p[0] == a.user_id][0][6])

    def _post_open(self, order, tasks):
        self.client.force_login(self.tu)
        self.client.post(reverse('room_manage'), {'school_class': '8A', 'minutes': 45, 'tasks': [t.id for t in tasks],
                                                  'order': ','.join(str(t.id) for t in order)})
        return StudyRoom.objects.get()

    def test_company_swap_task_and_teacher_fine(self):
        from .models import RoomAssignment, RoomFine
        b = make_task('Средняя2', 'B', Tag.objects.get(name='Циклы'))
        c = make_task('Сложная2', 'C', Tag.objects.get(name='Циклы'))
        room = self._post_open([self.tasks[0], b, c], [self.tasks[0], b, c])
        a = self.sts[0]
        act = reverse('room_act', args=[room.id])
        self.client.force_login(a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.assertEqual(self.client.post(act, {'a': 'swap'}).json()['error'], 'no_task')
        self.assertEqual(self.client.post(act, {'a': 'take'}).json()['task'], self.tasks[0].id)
        self.assertEqual(self.client.post(act, {'a': 'swap'}).json(), {'ok': True, 'task': b.id})   # первая → в конец очереди
        self.assertEqual(self.client.post(act, {'a': 'swap'}).json(), {'ok': True, 'task': c.id})
        self.assertEqual(self.client.post(act, {'a': 'swap'}).json()['error'], 'swap_limit')       # не больше 2 раз
        Submission.objects.create(student=a, task=c, status='DONE', is_graded=True, score=10)
        st = self.client.get(reverse('room_state', args=[room.id]) + '?fresh=1').json()
        self.assertEqual(st['me']['salary'], 30000)
        self.assertEqual(self.client.post(act, {'a': 'take'}).json()['task'], self.tasks[0].id)    # отложенные — снова, по порядку
        # штраф: только учитель
        self.assertEqual(self.client.post(act, {'a': 'fine', 'uid': a.user_id, 'amount': 5000}).json()['error'], 'bad')
        self.client.force_login(self.tu)
        self.assertEqual(self.client.post(act, {'a': 'fine', 'uid': a.user_id, 'amount': 5000, 'reason': 'шум'}).json(), {'ok': True})
        self.assertEqual(RoomFine.objects.get().amount, 5000)
        self.client.force_login(a.user)
        from django.core.cache import cache
        cache.delete(f'room:fresh:{a.user_id}')
        me = self.client.get(reverse('room_state', args=[room.id]) + '?fresh=1').json()['me']
        self.assertEqual((me['salary'], me['fines'], me['fine_last']), (25000, 5000, [5000, 'шум']))

    def test_company_places_get_10_9_8_coins_once(self):
        from .models import CoinTransaction, RoomAssignment, RoomFine
        room = self._post_open([self.tasks[0]], [self.tasks[0]])
        a, b, c = self.sts[:3]
        for st, pay in ((a, 30000), (b, 20000), (c, 10000)):
            RoomAssignment.objects.create(room=room, student=st, task=self.tasks[0], pay=pay, done_at=timezone.now())
        RoomFine.objects.create(room=room, student=a, amount=25000)          # 30000 − 25000 = 5000 → последнее место
        self.client.force_login(self.tu)
        self.client.post(reverse('room_close', args=[room.id]))
        room.refresh_from_db()
        self.assertTrue(room.top_awarded)
        got = dict(CoinTransaction.objects.filter(key=f'roomtop:{room.id}').values_list('student_id', 'amount'))
        self.assertEqual(got, {b.id: 10, c.id: 9, a.id: 8})
        self.client.post(reverse('room_close', args=[room.id]))             # повторно — без новых монет
        self.assertEqual(CoinTransaction.objects.filter(key=f'roomtop:{room.id}').count(), 3)

    def test_coin_rain_last_minute_50_total(self):
        from datetime import timedelta
        from django.core.cache import cache
        from .models import CoinTransaction
        room = self._post_open([self.tasks[0]], [self.tasks[0]])
        a, b = self.sts[0], self.sts[1]
        act = reverse('room_act', args=[room.id])
        self.client.force_login(a.user)
        self.assertEqual(self.client.post(act, {'a': 'coin', 'n': 0}).json()['error'], 'bad')        # ещё не последняя минута
        StudyRoom.objects.filter(pk=room.pk).update(ends_at=timezone.now() + timedelta(seconds=30))
        cache.delete(f'room:obj:{room.id}')
        self.assertEqual(self.client.post(act, {'a': 'coin', 'n': 3}).json(), {'ok': True})
        self.assertEqual(self.client.post(act, {'a': 'coin', 'n': 49}).json()['error'], 'bad')       # эта монета ещё не упала
        self.assertEqual(self.client.post(act, {'a': 'coin', 'n': 50}).json()['error'], 'bad')
        self.client.force_login(b.user)
        self.assertEqual(self.client.post(act, {'a': 'coin', 'n': 3}).json()['error'], 'taken')      # одну монету — один ученик
        self.assertEqual(CoinTransaction.objects.filter(key__startswith=f'rain:{room.id}:').count(), 1)
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        self.assertEqual(st['rain'], [3])
        self.assertIn('now', st)

    def test_dm_sender_name_shown_even_if_left(self):
        room = self._post_open([self.tasks[0]], [self.tasks[0]])
        a, b = self.sts[0], self.sts[1]
        self.client.force_login(a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.client.get(reverse('room_state', args=[room.id]))
        self.client.force_login(b.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.client.get(reverse('room_state', args=[room.id]))
        self.assertEqual(self.client.post(reverse('room_act', args=[room.id]), {'a': 'dm', 'to': a.user_id, 'text': 'привет'}).json().get('error'), None)
        self.client.force_login(a.user)
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        self.assertEqual(st['unread'], {str(b.user_id): 1})
        self.assertEqual(st['names'], {str(b.user_id): b.full_name})

    def test_company_tasks_from_boss_in_order_and_salary(self):
        from django.core.cache import cache
        from .models import RoomAssignment
        b = make_task('Средняя', 'B', Tag.objects.get(name='Циклы'))
        room = self._post_open([b, self.tasks[0]], [self.tasks[0], b])
        self.assertEqual(room.task_order, [b.id, self.tasks[0].id])                 # порядок — как выбрал руководитель
        a = self.sts[0]
        act = reverse('room_act', args=[room.id])
        self.client.force_login(a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.client.post(act, {'a': 'move', 'x': 3, 'y': 8, 'seat': 's1'})
        self.assertEqual(self.client.post(act, {'a': 'take'}).json(), {'ok': True, 'task': b.id})
        self.assertEqual(self.client.post(act, {'a': 'take'}).json()['error'], 'busy')     # пока не выполнил — нового нет
        st = self.client.get(reverse('room_state', args=[room.id]) + '?fresh=1').json()
        self.assertEqual(st['me'], {'cur': b.id, 'done': [], 'salary': 0, 'fines': 0, 'fine_last': None})
        Submission.objects.create(student=a, task=b, status='DONE', is_graded=True, score=10)
        cache.delete(f'room:fresh:{a.user_id}')
        st = self.client.get(reverse('room_state', args=[room.id]) + '?fresh=1').json()
        self.assertEqual(st['me'], {'cur': None, 'done': [b.id], 'salary': 20000, 'fines': 0, 'fine_last': None})
        self.assertIsNotNone(RoomAssignment.objects.get(student=a, task=b).done_at)
        row = [r for r in st['stats'] if r[0] == a.user_id][0]
        self.assertEqual((row[2], row[3]), (20000, 1))
        self.assertEqual(self.client.post(act, {'a': 'take'}).json()['task'], self.tasks[0].id)
        self.client.force_login(self.tu)
        self.assertIn('взял задание', self.client.get(reverse('room_state', args=[room.id])).json()['log'][-1][2])

    def test_company_from_course_subtopic_assistants_and_hints(self):
        """Учитель выбирает курс → тему → подтему: задачи подтемы, название компании — класс, ассистенты подсказывают."""
        from .models import Course, Subtopic, Topic
        c = Course.objects.create(name='Python')
        tp = Topic.objects.create(course=c, name='Цикл for')
        sub = Subtopic.objects.create(topic=tp, name='range()', theory_html='<p>range</p>')
        b = make_task('Шаги', 'B', Tag.objects.get(name='Циклы'))
        sub.tasks.set([b, self.tasks[0]])
        self.client.force_login(self.tu)
        page = self.client.get(reverse('room_manage')).content.decode()
        self.assertIn('"name": "range()"', page)
        self.client.post(reverse('room_manage'), {'school_class': '8A', 'minutes': 45, 'course': c.id, 'topic': tp.id, 'subtopic': sub.id})
        room = StudyRoom.objects.get()
        self.assertEqual((room.title, room.theory_subtopic_id), ('range()', sub.id))
        self.assertEqual(set(room.tasks.all()), {b, self.tasks[0]})
        self.assertEqual(room.task_order[0], self.tasks[0].id if self.tasks[0].level < 'B' else b.id)     # A → B → C
        a = self.sts[0]
        self.client.force_login(a.user)
        html = self.client.get(reverse('room_play', args=[room.id])).content.decode()
        self.assertIn("var CO_NAME = '8A'", html)
        self.tu.username = 'nurmash'; self.tu.save()                                  # наш учитель — с аватаром ағая
        self.client.force_login(self.tu)
        self.client.get(reverse('room_play', args=[room.id]))
        boss = next(r for r in self.client.get(reverse('room_state', args=[room.id])).json()['people'] if r[0] == self.tu.id)
        self.assertEqual(boss[1], 'Нұрмаш ағай'); self.assertIn('nurmash-agai', boss[10])
        self.client.force_login(a.user)
        self.assertIn('"assistants"', html)
        act = reverse('room_act', args=[room.id])
        self.assertEqual(self.client.post(act, {'a': 'hint'}).json()['error'], 'no_task')
        tid = self.client.post(act, {'a': 'take'}).json()['task']                     # подходить к руководителю не нужно
        h = self.client.post(act, {'a': 'hint'}).json()
        self.assertEqual(h['task'], tid); self.assertIsNone(h['fail'])
        t = Task.objects.get(id=tid)
        Submission.objects.create(student=a, task=t, status='DONE', autotest_passed=1, autotest_total=2,
                                  autotest_results=[{'ok': True}, {'ok': False, 'error': 'NameError: name x', 'input': '5'}])
        f = self.client.post(act, {'a': 'hint'}).json()['fail']
        self.assertEqual((f['n'], f['input'], f['passed']), (2, '5', 1)); self.assertIn('переменная', f['why'])

    def test_company_skips_tasks_solved_before_and_all_done(self):
        a = self.sts[0]
        Submission.objects.create(student=a, task=self.tasks[0], status='DONE', is_graded=True, score=10)
        room = self._post_open([self.tasks[0]], [self.tasks[0]])
        act = reverse('room_act', args=[room.id])
        self.client.force_login(a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.client.post(act, {'a': 'move', 'x': 4, 'y': 5})
        self.assertEqual(self.client.post(act, {'a': 'take'}).json()['error'], 'all_done')

    def test_lang_switch_and_fullscreen_exits_seen_by_teacher(self):
        room = self._open()
        a = self.sts[0]
        self.client.force_login(a.user)
        html = self.client.get(reverse('room_play', args=[room.id])).content.decode()
        self.assertIn('data-tr="Моя работа"', html)
        r = self.client.post(reverse('room_lang', args=[room.id]), {'lang': 'kk', 'keys': '["Моя работа"]'}).json()
        self.assertEqual(r['tr']['Моя работа'], UI_TRANSLATIONS['Моя работа'])
        self.assertEqual(r['txt']['fs_start'], UI_TRANSLATIONS['Войти в полноэкранном режиме'])
        self.assertEqual(len(r['tasks']), 2)
        act = reverse('room_act', args=[room.id])
        self.client.post(act, {'a': 'exit', 'kind': 'fullscreen'})
        self.client.post(act, {'a': 'exit', 'kind': 'hidden'})
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        self.assertEqual([r for r in st['stats'] if r[0] == a.user_id][0][5], 0)            # ученик чужие выходы не видит
        self.client.force_login(self.tu)
        st = self.client.get(reverse('room_state', args=[room.id])).json()
        self.assertEqual([r for r in st['stats'] if r[0] == a.user_id][0][5], 2)

    def test_theory_from_course_and_teacher_notes(self):
        from .models import Course, Topic
        course = Course.objects.create(name='Python')
        topic = Topic.objects.create(course=course, name='Циклы', theory_html='<h1>while</h1>', theory_video_url='https://youtu.be/abcdefghijk')
        self.client.force_login(self.tu)
        page = self.client.get(reverse('room_manage')).content.decode()
        self.assertIn('data-tag=', page)
        self.assertIn('Python — Циклы', page)
        self.client.post(reverse('room_manage'), {'school_class': '8A', 'minutes': 45, 'tasks': [self.tasks[0].id],
                                                  'theory_topic': topic.id, 'theory_text': 'i = 1\nwhile i <= 5:\n    i += 1'})
        room = StudyRoom.objects.get()
        self.assertEqual(room.theory_topic, topic)
        self.client.force_login(self.sts[0].user)
        html = self.client.get(reverse('room_play', args=[room.id])).content.decode()
        self.assertIn(reverse('room_theory', args=[room.id, 'topic', topic.id]), html)
        self.assertIn('youtube.com/embed/abcdefghijk', html)
        r = self.client.get(reverse('room_theory', args=[room.id, 'topic', topic.id]))
        self.assertContains(r, '<h1>while</h1>')
        self.assertIn('sandbox', r['Content-Security-Policy'])
        self.assertContains(self.client.get(reverse('room_theory', args=[room.id, 'text', 0])), 'while i &lt;= 5:')
        self.client.force_login(self.other.user)
        self.assertEqual(self.client.get(reverse('room_theory', args=[room.id, 'topic', topic.id])).status_code, 404)

    def test_state_poll_is_cheap(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        room = self._open()
        self.client.force_login(self.sts[0].user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.client.get(reverse('room_state', args=[room.id]))                      # прогрели кеш
        with CaptureQueriesContext(connection) as q:
            self.client.get(reverse('room_state', args=[room.id]))
        self.assertLessEqual(len(q), 2, [x['sql'] for x in q.captured_queries])     # пользователь (+ сессия) — и всё

    def test_label_shows_banner_and_avatar(self):
        room = self._open()
        a = self.sts[0]
        a.name_banner, a.avatar = 'vice', 'gta03'
        a.save()
        self.client.force_login(a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        me = [p for p in self.client.get(reverse('room_state', args=[room.id])).json()['people'] if p[0] == a.user_id][0]
        self.assertIn('b-vice', me[9])
        self.assertIn('gta-03', me[10])

    def test_close_room(self):
        room = self._open()
        self.client.post(reverse('room_close', args=[room.id]))
        room.refresh_from_db()
        self.assertIsNotNone(room.closed_at)
        self.client.force_login(self.sts[0].user)
        self.assertRedirects(self.client.get(reverse('room_play', args=[room.id])), reverse('codegames'), fetch_redirect_response=False)
        self.assertEqual(self.client.post(reverse('room_act', args=[room.id]), {'a': 'hand'}).json()['error'], 'closed')
        self.assertNotContains(self.client.get(reverse('all_tasks')), reverse('room_play', args=[room.id]))


class WeekendOfficeTests(TestCase):
    """Офис выходного дня: сб/вс 9–20, кабинеты параллелей, x2 монеты за решённое в офисе, топ-3 параллели — по 20 монет."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        tag = Tag.objects.create(name='Циклы')
        self.tA, self.tB, self.tC = make_task('A1', 'A', tag), make_task('B1', 'B', tag), make_task('C1', 'C', tag)
        self.sts = [Student.objects.create(user=User.objects.create_user(f'of{i}', password='p'), full_name=f'Офис {i}', school_class='8A')
                    for i in range(4)]
        self.kid = Student.objects.create(user=User.objects.create_user('ofkid', password='p'), full_name='Малыш', school_class='3A')
        from datetime import datetime
        self.sat = timezone.make_aware(datetime(2026, 11, 21, 12, 0))          # суббота, 12:00

    def _at(self, dt):
        from unittest import mock
        return mock.patch('django.utils.timezone.now', return_value=dt)

    def test_agai_teacher_is_himself_and_answers_own_classes(self):
        """Учитель-ағай заходит «собой» (не новым человеком); ученик пишет своему ағаю даже без него, чужому — нет."""
        from django.core.cache import cache
        from . import room as rm, office
        tilek = User.objects.create_user('tagbwoi', password='p', is_staff=True)       # ведёт 8A
        User.objects.create_user('yereke', password='p', is_staff=True)                 # ведёт 10–11, не 8A
        cache.clear()
        st = self.sts[0]
        with self._at(self.sat):
            self.client.force_login(st.user)
            page = self.client.get(reverse('office_play')).content.decode()
            self.assertIn('"mine": true', page)
            mine = [a for a in office.agai_list('8A') if a['mine']]
            self.assertEqual([a['key'] for a in mine], ['tilek'])
            ernar = next(a for a in office.agai_list('8A') if a['key'] == 'ernar')
            # своему — можно, хотя его нет в офисе; чужому — нельзя
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'dm', 'to': tilek.id, 'text': 'Ағай, вопрос'}).json()['ok'], True)
            cache.delete(f'room:{office.rid(self.sat.date())}:dmsay:{st.user_id}')
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'dm', 'to': ernar['uid'], 'text': 'Привет'}).json()['error'], 'gone')
            # учитель заходит — он и есть «Тілек ағай», а вопрос ученика — во «входящих»
            self.client.force_login(tilek)
            self.client.get(reverse('office_play'))
            d = self.client.get(reverse('office_state')).json()
            me = next(r for r in d['people'] if r[0] == tilek.id)
            self.assertEqual((me[1], me[11]), ('Тілек ағай', 'tilek'))
            self.assertIn("var MY_AG = 'tilek'", self.client.get(reverse('office_play')).content.decode())
            # вошёл ещё до обновления (без «ағая» в присутствии) — при входе на страницу всё равно становится им
            r_ = office.rid(self.sat.date())
            p_ = rm.get(r_, tilek.id); p_.pop('ag'); p_['n'] = 'Tilek Amankosov'
            from django.core.cache import cache as c_
            c_.set(rm._key(r_, tilek.id), p_, rm.TTL)
            self.client.get(reverse('office_play'))
            self.assertEqual(rm.get(r_, tilek.id)['ag'], 'tilek')
            self.assertEqual(d['unread'], {str(st.user_id): 1})
            from .office_views import _inbox
            self.assertEqual(_inbox(d['unread'], set()), [[st.user_id, st.full_name, 1]])   # если ученика уже нет в офисе
            # ответ ученику, которого сейчас нет в офисе
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'dm', 'to': st.user_id, 'text': 'Ответ'}).json()['ok'], True)

    def test_special_tags_hidden_from_lists_but_count_for_rewards(self):
        gta, _ = Tag.objects.get_or_create(name='GTA VI')
        t = make_task('Неон', 'A', gta)
        self.assertNotIn(gta, list(Tag.visible()))
        from . import banners
        Submission.objects.create(student=self.sts[0], task=t, status='DONE', is_graded=True, score=10)
        self.assertEqual(banners.progress(self.sts[0], 'GTA VI'), 1)            # челлендж по-прежнему считает

    def test_open_hours(self):
        from . import office
        self.assertTrue(office.is_open(self.sat))
        self.assertFalse(office.is_open(self.sat.replace(hour=20, minute=0)))
        self.assertFalse(office.is_open(self.sat.replace(hour=8, minute=59)))
        self.assertFalse(office.is_open(self.sat - timedelta(days=1)))                       # пятница
        self.assertTrue(office.is_open(self.sat + timedelta(days=1)))                        # воскресенье

    def test_solve_in_office_gives_double_coins_money_and_top3(self):
        from . import office
        from .models import CoinTransaction, OfficeSolve
        with self._at(self.sat):
            for i, st in enumerate(self.sts):
                self.client.force_login(st.user)
                r = self.client.get(reverse('office_play'))
                self.assertEqual(r.status_code, 200)
            # сесть в кабинет чужой параллели нельзя
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'move', 'x': 2, 'y': 2, 'seat': '9-0'}).json()['error'], 'other_grade')
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'move', 'x': 2, 'y': 2, 'seat': '8-0'}).json(), {'ok': True})
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'move', 'x': 2, 'y': 2, 'seat': 'adm-0'}).json()['error'], 'admin_only')
            teacher = User.objects.create_user('of_t', password='p', is_staff=True)
            self.client.force_login(teacher)
            self.assertContains(self.client.get(reverse('teacher_dashboard')), reverse('office_play'))
            self.assertContains(self.client.get(reverse('games_hub')), reverse('office_play'))
            self.assertContains(self.client.get(reverse('office_play')), '"adm-0"')
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'move', 'x': 2, 'y': 2, 'seat': 'adm-1'}).json(), {'ok': True})
            self.assertEqual(self.client.post(reverse('office_act'), {'a': 'move', 'x': 2, 'y': 2, 'seat': '8-1'}).json()['error'], 'other_grade')
            self.client.force_login(self.sts[3].user)
        later = self.sat + timedelta(minutes=5)
        Submission.objects.create(student=self.sts[0], task=self.tC, status='DONE', is_graded=True, score=10)
        Submission.objects.create(student=self.sts[1], task=self.tB, status='DONE', is_graded=True, score=10)
        Submission.objects.create(student=self.sts[2], task=self.tA, status='DONE', is_graded=True, score=10)
        Submission.objects.create(student=self.sts[3], task=self.tA, status='DONE', is_graded=True, score=6)   # не решена
        Submission.objects.filter(score=10).update(updated_at=later)
        with self._at(later):
            office.collect(self.sat.date())
        self.assertEqual(OfficeSolve.objects.count(), 3)
        self.assertEqual(CoinTransaction.objects.get(student=self.sts[0], key=f'office:{self.tC.id}').amount, 3)     # +3 сверху к обычным 3
        rows = office.stats(self.sat.date())['8']
        self.assertEqual([r[1] for r in rows], [30000, 20000, 10000])
        with self._at(later):
            self.client.force_login(self.sts[0].user)
            st = self.client.get(reverse('office_state') + '?fresh=1').json()
            self.assertEqual(st['ome']['money'], 30000)
            self.assertEqual(st['stats'][0][2], 30000)
        # итоги дня — после 20:00
        office.finalize_due(self.sat.replace(hour=19))
        self.assertFalse(CoinTransaction.objects.filter(key__startswith='officetop:').exists())
        office.finalize_due(self.sat.replace(hour=20, minute=1))
        tops = CoinTransaction.objects.filter(key=f'officetop:{self.sat.date()}')
        self.assertEqual(sorted(t.student_id for t in tops), sorted(s.id for s in self.sts[:3]))
        self.assertTrue(all(t.amount == 20 for t in tops))
        office.finalize_due(self.sat.replace(hour=21))                                         # второй раз — ничего
        self.assertEqual(CoinTransaction.objects.filter(key__startswith='officetop:').count(), 3)

    def test_teacher_gives_3_tasks_where_stopped_and_bonus(self):
        from . import office
        from .models import OfficePackage
        tag = Tag.objects.get(name='Циклы')
        extra = [make_task(f'A{i}', 'A', tag) for i in range(2, 5)]            # A1..A4 + B1, C1
        st = self.sts[0]
        Submission.objects.create(student=st, task=self.tA, status='DONE', is_graded=True, score=10)   # A1 уже решена
        Submission.objects.filter(student=st, task=self.tA).update(updated_at=self.sat - timedelta(hours=1))
        with self._at(self.sat):
            self.client.force_login(st.user)
            page = self.client.get(reverse('office_play')).content.decode()
            self.assertNotIn('id="olist"', page)                                  # свободного списка задач нет — задачи даёт преподаватель
            topics = self.client.get(reverse('office_bot') + '?q=topics').json()['topics']
            t = [x for x in topics if x['name'] == 'Циклы'][0]
            self.assertEqual(t['level'], 'A')
            act = reverse('office_act')
            self.assertEqual(office.layout(office.grades())['managers'], [])         # руководителей больше нет
            r = self.client.post(act, {'a': 'package', 'tag': tag.id}).json()
            self.assertEqual(r['tasks'], [x.id for x in extra])                   # следующие 3 нерешённые на уровне A
            self.assertEqual(self.client.post(act, {'a': 'package', 'tag': tag.id}).json()['error'], 'pkg_busy')
        for x in extra:
            Submission.objects.create(student=st, task=x, status='DONE', is_graded=True, score=10)
        Submission.objects.filter(student=st, task__in=extra).update(updated_at=self.sat + timedelta(minutes=3))
        with self._at(self.sat + timedelta(minutes=4)):
            office.collect(self.sat.date())
        pkg = OfficePackage.objects.get(student=st)
        self.assertEqual(pkg.bonus, 10000)
        self.assertEqual(office.stats(self.sat.date())['8'][0][1], 3 * 10000 + 10000)   # 3 задачи A + премия

    def test_admin_bot_advice_and_theory(self):
        from .models import Course, Topic
        st = self.sts[0]
        self.client.force_login(st.user)
        r = self.client.get(reverse('office_bot') + '?q=advice').json()
        self.assertEqual(r['level'], 'A')                                                   # новичку — лёгкая задача
        self.assertEqual(Task.objects.get(id=r['task']).level, 'A')
        Submission.objects.create(student=st, task_id=r['task'], status='DONE', is_graded=True, score=10)
        self.assertNotEqual(self.client.get(reverse('office_bot') + '?q=advice').json()['task'], r['task'])   # решённую не советует
        course = Course.objects.create(name='Python', is_visible=True)
        tp = Topic.objects.create(course=course, name='Циклы', theory_html='<p>for</p>')
        tp.tasks.add(self.tB)
        items = self.client.get(reverse('office_bot') + f'?q=theory&task={self.tB.id}').json()['items']
        self.assertTrue(all('Python' in i['title'] for i in items))

    def test_solved_before_entering_not_counted_and_closed_on_weekdays(self):
        from . import office
        from .models import OfficeSolve
        Submission.objects.create(student=self.sts[0], task=self.tA, status='DONE', is_graded=True, score=10)
        Submission.objects.filter(student=self.sts[0]).update(updated_at=self.sat - timedelta(minutes=30))
        with self._at(self.sat):
            self.client.force_login(self.sts[0].user)
            self.client.get(reverse('office_play'))
            Submission.objects.filter(student=self.sts[0]).update(updated_at=self.sat - timedelta(minutes=30))
            office.collect(self.sat.date())
        self.assertFalse(OfficeSolve.objects.exists())
        with self._at(self.sat - timedelta(days=1)):
            self.assertRedirects(self.client.get(reverse('office_play')), reverse('codegames'), fetch_redirect_response=False)
            self.client.force_login(self.kid.user)
        with self._at(self.sat):
            self.client.force_login(self.kid.user)
            self.assertRedirects(self.client.get(reverse('office_play')), reverse('codegames'), fetch_redirect_response=False)


class RoomDmTests(TestCase):
    """Личные сообщения в «Компании» и офисе: нажал на человека — написал ему."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.tu = User.objects.create_user('dm_t', password='p', is_staff=True, is_superuser=True)
        self.a = Student.objects.create(user=User.objects.create_user('dm_a', password='p'), full_name='Аа', school_class='8A')
        self.b = Student.objects.create(user=User.objects.create_user('dm_b', password='p'), full_name='Бб', school_class='8A')
        self.task = make_task('Т', 'A', Tag.objects.create(name='x'))

    def test_dm_between_student_and_teacher(self):
        self.client.force_login(self.tu)
        self.client.post(reverse('room_manage'), {'school_class': '8A', 'minutes': 45, 'tasks': [self.task.id]})
        room = StudyRoom.objects.get()
        act, state = reverse('room_act', args=[room.id]), reverse('room_state', args=[room.id])
        self.client.get(reverse('room_play', args=[room.id]))                                   # учитель в зале
        self.client.force_login(self.a.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.assertEqual(self.client.post(act, {'a': 'dm', 'to': self.b.user_id, 'text': 'привет'}).json()['error'], 'gone')   # Бб нет в зале
        r = self.client.post(act, {'a': 'dm', 'to': self.tu.id, 'text': 'Как решить задачу 2?'}).json()
        self.assertEqual(r['conv'][0][:2], [self.a.user_id, 'Как решить задачу 2?'])
        self.client.force_login(self.tu)
        st = self.client.get(state).json()
        self.assertEqual(st['unread'], {str(self.a.user_id): 1})
        st = self.client.get(state + f'?dm={self.a.user_id}').json()
        self.assertEqual(st['conv'][1][0][1], 'Как решить задачу 2?')
        self.assertEqual(self.client.get(state).json()['unread'], {})                       # прочитано
        self.assertTrue(self.client.post(act, {'a': 'dm', 'to': self.a.user_id, 'text': 'Используй цикл for'}).json()['ok'])
        self.client.force_login(self.b.user)
        self.client.get(reverse('room_play', args=[room.id]))
        self.assertNotIn('conv', self.client.get(state).json())
        self.assertEqual(self.client.get(state + f'?dm={self.a.user_id}').json()['conv'][1], [])   # чужую переписку не видно


class RoomPresenceSelfHealTests(TestCase):
    """Ученик не должен «пропадать» из зала, если список присутствующих потерялся в кеше."""

    def test_lost_ids_list_is_restored(self):
        from django.core.cache import cache
        from . import room as rm
        rm.touch(9901, 5, {'name': 'A'}, now=1000.0)
        self.assertIn(5, rm.people(9901, now=1001.0))
        cache.delete(rm._ids_key(9901))                       # список потерялся
        self.assertNotIn(5, rm.people(9901, now=1002.0))
        rm.touch(9901, 5, {'name': 'A'}, now=1010.0)          # следующий опрос ученика
        self.assertIn(5, rm.people(9901, now=1011.0))


from django.test import override_settings as _ovs  # noqa: E402


@_ovs(DAILY_VISIT_COIN=True)
class DailyVisitCoinTests(TestCase):
    def test_one_coin_per_day(self):
        from django.core.cache import cache
        from .models import CoinTransaction
        u = User.objects.create_user('vis', password='p')
        st = Student.objects.create(user=u, full_name='В', school_class='7A')
        cache.clear()
        self.client.force_login(u)
        self.client.get('/')
        self.client.get('/')
        cache.clear()                       # даже если кеш очистился — второй монеты за день нет
        self.client.get('/profile/')
        self.assertEqual(list(CoinTransaction.objects.filter(student=st, kind='login').values_list('amount', flat=True)), [1])
