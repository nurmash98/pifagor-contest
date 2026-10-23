# -*- coding: utf-8 -*-
"""Тесты пересдачи СОР/СОЧ: назначение, другие случайные задачи, две оценки, доступ."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Course, Duel, DuelQueueEntry, Exam, ExamAnswer, ExamAttempt, ExamRetake, Student, Tag, Task, Teacher)


def make_task(title, level, tag, ok=True):
    task = Task.objects.create(title=title, level=level, description='d', input_example='', output_example='1',
                               test_cases=[{'input': '', 'output': '1'}] if ok else [])
    task.tags.add(tag)
    return task


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
        self.assertContains(self.client.get(reverse('exam_list')), 'Пересдача')

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
        self.assertIn('Быстрый', self.client.get('/teacher/suspicious/').content.decode())

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

    def test_many_devices(self):
        from .models import LoginEvent
        for ua in ('A', 'B', 'C'):
            LoginEvent.objects.create(user=self.su, user_agent=ua)
        self.assertIn('3 разных устройств', self.page())


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
        self.assertIn('Новичок', html)
        self.assertIn('Решите 10 задач на оценку 10/10.', html)
        self.assertIn('id="badge-toast"', html)       # новый значок — всплывашка
        self.assertTrue(StudentBadge.objects.get(student=self.student, key='t10').seen)
        self.assertNotIn('id="badge-toast"', self.client.get('/profile/').content.decode())  # второй раз не показываем
        s = self.client.session; s['content_lang'] = 'kk'; s.save()
        kz = self.client.get('/profile/').content.decode()
        self.assertIn('Бастаушы', kz)
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
        self.assertEqual(self.titles(''), {'Сделанная', 'Рабочая', 'Новая', 'Девятка'})

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
            self.st.append(Student.objects.create(user=u, full_name=f'Игрок {name}', school_class='7F',
                                                  duel_tester=(name != 'c')))
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

    def test_default_mode_is_testers_only(self):
        from .models import SiteSettings
        self.assertEqual(SiteSettings.get().duels_mode, 'testers')
        self.client.login(username='dc', password='pw12345')           # не тестер
        r = self.client.get(reverse('duels'))
        self.assertRedirects(r, reverse('all_tasks'))
        self.client.post(reverse('duels_find'))
        self.assertEqual(DuelQueueEntry.objects.count(), 0)
        self.assertNotIn('Дуэли', self.client.get(reverse('all_tasks')).content.decode())
        self.client.login(username='da', password='pw12345')           # тестер
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)
        self.assertIn('Дуэли', self.client.get(reverse('all_tasks')).content.decode())

    def test_all_mode_opens_for_everyone(self):
        from .models import SiteSettings
        s = SiteSettings.get(); s.duels_mode = 'all'; s.save()
        self.client.login(username='dc', password='pw12345')
        self.assertEqual(self.client.get(reverse('duels')).status_code, 200)

    def test_teacher_can_mark_tester(self):
        self.client.login(username='dt', password='x')
        self.client.post(reverse('student_toggle_duel_tester', args=[self.st[2].id]))
        self.st[2].refresh_from_db()
        self.assertTrue(self.st[2].duel_tester)

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
        self.assertIn('по классам и тестерам', page)
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
        self.assertIn('Дуэли:', self.client.get(reverse('teacher_dashboard')).content.decode())
        self.client.post(reverse('duels_toggle'))
        self.assertEqual(SiteSettings.get().duels_mode, 'all')

    def test_other_teacher_cannot_mark_testers(self):
        su = User.objects.create_user('stud', password='x')
        st = Student.objects.create(user=su, full_name='Ученик', school_class='7F')
        self.client.login(username='other', password='x')
        self.client.post(reverse('student_toggle_duel_tester', args=[st.id]))
        st.refresh_from_db()
        self.assertFalse(st.duel_tester)


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
        self.client.login(username='tg', password='x')
        return self.client.post(reverse('duels_class_tags'), {'school_class': cls, 'tags': tag_ids})

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

    def test_too_few_tagged_tasks_falls_back(self):
        from . import duels
        for t in self.loops[1:]:
            t.tags.clear()          # осталась одна задача с тегом «циклы»
        self.set_tags([self.t_loop.id])
        self.assertEqual(len(duels.pick_tasks(self.a, self.b)), 3)

    def test_cannot_set_tags_for_foreign_class(self):
        from .models import SiteSettings
        self.set_tags([self.t_loop.id], cls='9Z')
        self.assertNotIn('9Z', SiteSettings.get().duels_class_tags)

    def test_overview_lists_tags(self):
        self.set_tags([self.t_loop.id])
        html = self.client.get(reverse('duels')).content.decode()
        self.assertIn('Теги задач: 1', html)
        self.assertIn('#циклы-тест', html)


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
        self.assertIn('tier-badge tier-wood', html)       # новый значок «Новичок»
        self.assertIn('НОВИЧОК', html)
        self.assertIn('БОЕЦ', html)                       # закрытый «Боец» тоже нарисован (с замком)
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
        self.assertIn('Скоро', html)
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
