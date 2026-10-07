# -*- coding: utf-8 -*-
"""Тесты пересдачи СОР/СОЧ: назначение, другие случайные задачи, две оценки, доступ."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Exam, ExamAnswer, ExamAttempt, ExamRetake, Student, Tag, Task, Teacher)


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

    def test_student_already_in_retake_cannot_be_added_again(self):
        self.retake(self.exam, [self.students[0]], start_delta=60)
        self.client.force_login(self.teacher_user)
        future = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('retake_create', args=[self.exam.id]),
                         {'students': [self.students[0].id], 'start_at': future, 'duration_minutes': '30'})
        self.assertEqual(ExamRetake.objects.count(), 1)

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
