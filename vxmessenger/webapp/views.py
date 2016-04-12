from django.shortcuts import render
from django.http import HttpResponse


def privacy(request):
    return render(request, 'privacy.html', {
    })


def home(request):
    return render(request, 'home.html', {
    })


def challenge(request):
    # Rather use vxmessenger.webhook from the command line
    return HttpResponse(request.GET.get('hub.challenge'))
