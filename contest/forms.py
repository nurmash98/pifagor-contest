from django import forms

from .models import normalize_school_class

GRADE_CHOICES = [('', 'Класс')] + [(str(n), str(n)) for n in range(1, 12)]
LETTER_CHOICES = [('', 'Литер')] + [(ch, ch) for ch in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ']


class StudentRegistrationForm(forms.Form):
    username = forms.CharField(max_length=150, label='Никнейм', widget=forms.TextInput(attrs={'class': 'form-control'}))
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    full_name = forms.CharField(max_length=150, label='Имя и Фамилия', widget=forms.TextInput(attrs={'class': 'form-control'}))
    # Класс выбирается из списков (цифра + английский литер), а не печатается —
    # так у всех учеников одного класса он записан одинаково, например «7F».
    grade = forms.ChoiceField(choices=GRADE_CHOICES, label='Класс', widget=forms.Select(attrs={'class': 'form-select'}))
    letter = forms.ChoiceField(choices=LETTER_CHOICES, label='Литер', widget=forms.Select(attrs={'class': 'form-select'}))

    def clean(self):
        data = super().clean()
        if data.get('grade') and data.get('letter'):
            data['school_class'] = normalize_school_class(data['grade'] + data['letter'])
        return data
