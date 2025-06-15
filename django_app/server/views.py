from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.hashers import check_password
from .models import PollUser, Group
from .serializers import RegisterSerializer, LoginSerializer, SendPollSerializer
import requests
import os
from datetime import datetime
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

TELEGRAM_API = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN')}"


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
        print("", update)
        message = update.get("message", {})
        fetch_date = message.get('date') 
        text = message.get("text", "")
        chat = message.get("chat", {})        
        chat_id = chat.get("id")
        
        match text:
            case "/start":
                requests.post(f"{TELEGRAM_API}/sendMessage", json={
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

            case "/bind":
                if (chat_type := chat.get('type')) and chat_type != 'group':
                    return Response({"status": 'bad request'}) 

                chat_title = chat.get("title")
                chat_description = None

                if opt_chat_description := chat.get('description'):
                    chat_description = opt_chat_description

                chat_photo_url = None
                if opt_chat_photo := chat.get("photo"):
                    if photo_id := opt_chat_photo.get('big_file_id'):
                        if (response := requests.get(f'{TELEGRAM_API}/getFile', params={'file_id': photo_id})) and response.status_code == 200:
                            if (json_response := response.json()) and json_response.get('ok'):
                                file_path = json_response.get('result').get('file_path')
                                chat_photo_url = f'{TELEGRAM_API}/{file_path}'

                group, _ = Group.objects.get_or_create(chat_id=chat_id)
                group.fetch_date = datetime.fromtimestamp(fetch_date)
                group.title = chat_title
                group.description = chat_description
                group.photo_url = chat_photo_url
                group.save()

        return Response({"status": "ok"})
    
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
        print("Dados recebidos no registro:", data)

        serializer = RegisterSerializer(data=data)
        
        if not serializer.is_valid():
            print("Erros de validação:", serializer.errors)
            return Response({
                "success": False,
                "message": "Erro na validação dos dados.",
                "errors": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not serializer.validated_data.get('is_professor'):
            return Response({
                "success": False,
                "message": "Somente professores podem se cadastrar.",
                "errors": {"is_professor": ["Permissão negada para este tipo de usuário."]}
            }, status=status.HTTP_403_FORBIDDEN)
        
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        
        return Response({
            "success": True,
            "message": "Usuário registrado com sucesso.",
            "data": {
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh)
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
                "success": False,
                "message": "Erro na validação dos dados.",
                "errors": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        try:
            user = PollUser.objects.get(email=email)
        except PollUser.DoesNotExist:
            return Response({
                "success": False,
                "message": "Credenciais inválidas.",
                "errors": {"email": ["Usuário não encontrado."]}
            }, status=status.HTTP_401_UNAUTHORIZED)

        if not check_password(password, user.password):
            return Response({
                "success": False,
                "message": "Credenciais inválidas.",
                "errors": {"password": ["Senha incorreta."]}
            }, status=status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)
        return Response({
            "success": True,
            "message": "Login realizado com sucesso.",
            "data": {
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh)
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
                "success": False,
                "message": "Erro de validação nos dados enviados.",
                "errors": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        chat_id = serializer.validated_data['chatId']
        question = serializer.validated_data['question']
        options = serializer.validated_data['options']

        try:
            response = requests.post(f"{TELEGRAM_API}/sendPoll", json={
                "chat_id": chat_id,
                "question": question,
                "options": options,
                "is_anonymous": False,
            })

            if response.status_code == 200:
                return Response({
                    "success": True,
                    "message": "Enquete enviada com sucesso.",
                    "data": {
                        "chat_id": chat_id,
                        "question": question,
                        "options": options
                    }
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "success": False,
                    "message": "Erro na API do Telegram.",
                    "errors": response.json()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except requests.RequestException as e:
            return Response({
                "success": False,
                "message": "Falha na comunicação com o Telegram.",
                "errors": {"exception": str(e)}
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

      
class SendQuizView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Envia um quiz (enquete com resposta correta) para um grupo do Telegram.",
        request_body=SendPollSerializer,
        responses={
            200: openapi.Response(description="Quiz enviado com sucesso."),
            400: "Erro de validação.",
            500: "Erro ao enviar para o Telegram."
        }
    )

    def post(self, request):
        serializer = SendPollSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                "success": False,
                "message": "Erro de validação nos dados.",
                "errors": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        chat_id = serializer.validated_data['chatId']
        question = serializer.validated_data['question']
        options = serializer.validated_data['options']
        correct_option = serializer.validated_data.get("correctOption")

        if correct_option is None or correct_option < 0 or correct_option >= len(options):
            return Response({
                "success": False,
                "message": "Índice da opção correta inválido.",
                "errors": {"correctOption": "Informe um índice válido de opção correta."}
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            response = requests.post(f"{TELEGRAM_API}/sendPoll", json={
                "chat_id": chat_id,
                "question": question,
                "options": options,
                "is_anonymous": False,
                "type": "quiz",
                "correct_option_id": correct_option,
            })

            if response.status_code == 200:
                return Response({
                    "success": True,
                    "message": "Quiz enviado com sucesso.",
                    "data": {
                        "chat_id": chat_id,
                        "question": question,
                        "correctOption": correct_option
                    }
                }, status=status.HTTP_200_OK)

            return Response({
                "success": False,
                "message": "Erro na API do Telegram.",
                "errors": response.json()
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except requests.RequestException as e:
            return Response({
                "success": False,
                "message": "Erro de conexão com o Telegram.",
                "errors": {"exception": str(e)}
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


