from django.shortcuts import render


def privacy(request):
    return render(request, 'privacy.html', {
    })


def home(request):
    return render(request, 'home.html', {
    })
