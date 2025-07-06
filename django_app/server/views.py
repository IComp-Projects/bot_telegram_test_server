from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.hashers import check_password
from .models import PollUser, Group 
from .serializers import RegisterSerializer, LoginSerializer, SendPollSerializer, SendQuizSerializer, GroupSerializer, BindGroupSerializer
import requests
import os

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .tasks import send_quiz_task
from datetime import datetime
from django.utils.timezone import make_aware
from django.conf import settings


class PingView(APIView):
    @swagger_auto_schema(
        operation_description="Endpoint de monitoramento para manter o servidor acordado.",
        responses={200: openapi.Response(description="Servidor ativo.")}
    )
    def get(self, request):
        return Response({"data": {"status": "alive"}})


class TelegramWebhookView(APIView):
    @swagger_auto_schema(
        operation_description="Recebe atualizações do Telegram Bot.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'message': openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'text': openapi.Schema(type=openapi.TYPE_STRING),
                        'chat': openapi.Schema(type=openapi.TYPE_OBJECT)
                    }
                )
            }
        ),
        responses={200: openapi.Response(description="Atualização recebida com sucesso.")}
    )


    def post(self, request):
        update = request.data
        message = update.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        

        if text == "/start":
            requests.post(f"{settings.TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "Vamos começar 🖥️\nUse o botão abaixo para criar uma enquete!",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Criar enquete",
                                "web_app": {"url": "https://poll-miniapp.vercel.app/"}
                            }
                        ]
                    ]
                }
            })

        if text == "/bind":
            if (chat_type := chat.get('type')) and chat_type != 'group':
                return Response({
                    "status": 'bad request'},
                    status=status.HTTP_400_BAD_REQUEST)

            chat_title = chat.get("title")

            bind_response = requests.post(f'https://bot-telegram-test-server1.onrender.com/api/groups/', json={
                'chat_id': chat_id,
                'chat_title': chat_title
            })

            if not bind_response.ok:
                requests.post(f"{settings.TELEGRAM_API}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"Erro! Não foi possível salvar o grupo {chat_title}.",
                })
                return Response({"data": {"status":bind_response.status_code}})
            
            requests.post(f"{settings.TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"Sucesso! O grupo {chat_title} foi salvo.",
            })

        return Response({"data": {"status": "ok"}})


class RegisterView(APIView):
    @swagger_auto_schema(
        operation_description="Cria um novo usuário professor.",
        request_body=RegisterSerializer,
        responses={
            201: openapi.Response(description="Usuário registrado com sucesso."),
            400: "Dados inválidos.",
            403: "Somente professores podem se cadastrar."
        }
    )


    def post(self, request):
        data = request.data
        serializer = RegisterSerializer(data=data)

        if not serializer.is_valid():
            return Response({
                "data": {
                    "success": False,
                    "message": "Erro na validação dos dados.",
                    "errors": serializer.errors
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        if not serializer.validated_data.get('is_professor'):
            return Response({
                "data": {
                    "success": False,
                    "message": "Somente professores podem se cadastrar.",
                    "errors": {"is_professor": ["Permissão negada para este tipo de usuário."]}
                }
            }, status=status.HTTP_403_FORBIDDEN)

        user = serializer.save()
        refresh = RefreshToken.for_user(user)

        return Response({
            "data": {
                "success": True,
                "message": "Usuário registrado com sucesso.",
                "tokens": {
                    "access_token": str(refresh.access_token),
                    "refresh_token": str(refresh)
                }
            }
        }, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    @swagger_auto_schema(
        operation_description="Autentica um usuário e retorna um token JWT.",
        request_body=LoginSerializer,
        responses={
            200: openapi.Response(description="Login realizado com sucesso."),
            400: "Dados inválidos.",
            401: "Credenciais inválidas."
        }
    )


    def post(self, request):
        serializer = LoginSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                "data": {
                    "success": False,
                    "message": "Erro na validação dos dados.",
                    "errors": serializer.errors
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        try:
            user = PollUser.objects.get(email=email)
        except PollUser.DoesNotExist:
            return Response({
                "data": {
                    "success": False,
                    "message": "Credenciais inválidas.",
                    "errors": {"email": ["Usuário não encontrado."]}
                }
            }, status=status.HTTP_401_UNAUTHORIZED)

        if not check_password(password, user.password):
            return Response({
                "data": {
                    "success": False,
                    "message": "Credenciais inválidas.",
                    "errors": {"password": ["Senha incorreta."]}
                }
            }, status=status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)
        return Response({
            "data": {
                "success": True,
                "message": "Login realizado com sucesso.",
                "tokens": {
                    "access_token": str(refresh.access_token),
                    "refresh_token": str(refresh)
                }
            }
        }, status=status.HTTP_200_OK)


class SendPollView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Envia uma enquete para um grupo do Telegram.",
        request_body=SendPollSerializer,
        responses={
            200: openapi.Response(description="Poll enviada com sucesso!"),
            400: "Erro de validação.",
            500: "Erro ao enviar a Poll."
        }
    )

    
    def post(self, request):
        serializer = SendPollSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                "data": {
                    "success": False,
                    "message": "Erro de validação nos dados enviados.",
                    "errors": serializer.errors
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        chat_id = serializer.validated_data['chatId']
        question = serializer.validated_data['question']
        options = serializer.validated_data['options']

        try:
            response = requests.post(f"{settings.TELEGRAM_API}/sendPoll", json={
                "chat_id": chat_id,
                "question": question,
                "options": options,
                "is_anonymous": False,
            })

            if response.status_code == 200:
                return Response({
                    "data": {
                        "success": True,
                        "message": "Enquete enviada com sucesso.",
                        "poll": {
                            "chat_id": chat_id,
                            "question": question,
                            "options": options
                        }
                    }
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "data": {
                        "success": False,
                        "message": "Erro na API do Telegram.",
                        "errors": response.json()
                    }
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except requests.RequestException as e:
            return Response({
                "data": {
                    "success": False,
                    "message": "Falha na comunicação com o Telegram.",
                    "errors": {"exception": str(e)}
                }
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SendQuizView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Envia múltiplas perguntas tipo quiz para um grupo do Telegram. Pode ser agendado.",
        request_body=SendQuizSerializer,
        responses={
            200: openapi.Response(description="Quizzes enviados ou agendados com sucesso."),
            400: "Erro de validação.",
            500: "Erro ao enviar para o Telegram."
        }
    )
    def post(self, request):
        serializer = SendQuizSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                "data": {
                    "success": False,
                    "message": "Erro de validação nos dados enviados.",
                    "errors": serializer.errors
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        chat_id = serializer.validated_data['chatId']
        questions = serializer.validated_data['questions']
        schedule_date = serializer.validated_data.get('schedule_date')
        schedule_time = serializer.validated_data.get('schedule_time')

        if schedule_date and schedule_time:
            try:
                scheduled_datetime = make_aware(datetime.combine(schedule_date, schedule_time))
                send_quiz_task.apply_async((chat_id, questions), eta=scheduled_datetime)
                return Response({
                    "data": {
                        "success": True,
                        "message": f"Quizzes agendados para {scheduled_datetime.strftime('%d/%m/%Y %H:%M')}."
                    }
                }, status=status.HTTP_200_OK)
            except Exception as e:
                return Response({
                    "data": {
                        "success": False,
                        "message": "Erro ao agendar envio dos quizzes.",
                        "errors": str(e)
                    }
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            send_quiz_task.delay(chat_id, questions)
            return Response({
                "data": {
                    "success": True,
                    "message": "Quizzes enviados imediatamente."
                }
            }, status=status.HTTP_200_OK)


class GroupsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Lista todos os grupos vinculados ao usuário autenticado.",
        responses={200: openapi.Response(description="Lista de grupos vinculados.")}
    )
    def get(self, request):
        groups = request.user.groups.all()
        serializer = GroupSerializer(groups, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        operation_description="Vincula um grupo do Telegram a um professor e/ou staff autenticado no sistema.",
        request_body=BindGroupSerializer,
        responses={
            200: openapi.Response(description="Grupo vinculado com sucesso ou vínculo já existente."),
            400: "Requisição inválida.",
            403: "Usuário com permissões insuficientes para vincular grupos.",
            404: "Usuário ou grupo não encontrados."
        }
    )
    def post(self, request):
        serializer = BindGroupSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                "data": {
                    "success": False,
                    "message": "Erro de validação nos dados enviados.",
                    "errors": serializer.errors
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        chat_id = serializer.validated_data['chatId']
        chat_title = serializer.validated_data['chatTitle']

        if not (request.user.is_professor or request.user.is_staff):
            return Response({
                "success": False,
                "message": "Permissões insuficientes. Usuário não pode vincular grupos."
            }, status=status.HTTP_403_FORBIDDEN)

        group, _ = Group.objects.update_or_create(
            chat_id=chat_id,
            defaults={'title': chat_title}
        )

        vinculo_ativo = request.user.groups.filter(id=group.id).exists()
        if not vinculo_ativo:
            request.user.groups.add(group)

        return Response({
            "success": True,
            "message": "Grupo vinculado com sucesso." if not vinculo_ativo else "Vínculo ativo. Atualizando dados do grupo.",
            "data": {
                "fetch_date": group.fetch_date
            }
        }, status=status.HTTP_200_OK)

