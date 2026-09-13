from django import forms

class StudentRegistrationForm(forms.Form):
    username = forms.CharField(max_length=150, label='Логин', widget=forms.TextInput(attrs={'class': 'form-control'}))
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    full_name = forms.CharField(max_length=150, label='Имя и Фамилия', widget=forms.TextInput(attrs={'class': 'form-control'}))
    school_class = forms.CharField(max_length=10, label='Класс (например, 10А)', widget=forms.TextInput(attrs={'class': 'form-control'}))