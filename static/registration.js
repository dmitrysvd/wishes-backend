const { Connect, Config, ConnectEvents } = window.SuperAppKit;

const appId = window.appId;
const authRedirectUrl = window.authRedirectUrl;

Config.init({
  appId: appId, // идентификатор приложения
});

// url должен иметь https схему! При использовании http будет бесконечная загрузка
// В url в query-string передастся параметр payload с данными авторизации, формат данных в payload - VKSilentTokenPayload.
// Если передать параметр state, то после успешной авторизации в url
// в query-string будет передан параметр state имеющий такое же значение
// как и переданное в Connect.redirectAuth метод.
// Connect.redirectAuth({ url: authRedirectUrl, state: '1234'});

const oneTapButton = Connect.buttonOneTapAuth({
  // Обязательный параметр в который нужно добавить обработчик событий приходящих из SDK
  callback: function(e) {
    const type = e.type;

    if (!type) {
      return false;
    }

    switch (type) {
      case ConnectEvents.OneTapAuthEventsSDK.LOGIN_SUCCESS: // = 'VKSDKOneTapAuthLoginSuccess'
        console.log(e);
        return false
      // Для этих событий нужно открыть полноценный VK ID чтобы
      // пользователь дорегистрировался или подтвердил телефон
      case ConnectEvents.OneTapAuthEventsSDK.FULL_AUTH_NEEDED: //  = 'VKSDKOneTapAuthFullAuthNeeded'
      case ConnectEvents.OneTapAuthEventsSDK.PHONE_VALIDATION_NEEDED: // = 'VKSDKOneTapAuthPhoneValidationNeeded'
      case ConnectEvents.ButtonOneTapAuthEventsSDK.SHOW_LOGIN: // = 'VKSDKButtonOneTapAuthShowLogin'
        return Connect.redirectAuth({ url: authRedirectUrl, state: 'dj29fnsadjsd82'}); // url - строка с url, на который будет произведён редирект после авторизации.
        // state - состояние вашего приложение или любая произвольная строка, которая будет добавлена к url после авторизации.
      // Пользователь перешел по кнопке "Войти другим способом"
      case ConnectEvents.ButtonOneTapAuthEventsSDK.SHOW_LOGIN_OPTIONS: // = 'VKSDKButtonOneTapAuthShowLoginOptions'
        // Параметр url: ссылка для перехода после авторизации. Должен иметь https схему. Обязательный параметр.
        return Connect.redirectAuth({ url: authRedirectUrl });
    }

    return false;
  },
  // Не обязательный параметр с настройками отображения OneTap
  options: {
    showAlternativeLogin: true, // Отображение кнопки "Войти другим способом"
    displayMode: 'default', // Режим отображения кнопки 'default' | 'name_phone' | 'phone_name'
    buttonStyles: {
      borderRadius: 8, // Радиус скругления кнопок
    }
  },
});

// Получить iframe можно с помощью метода getFrame()
document.getElementById('vk-auth-button').appendChild(oneTapButton.getFrame());

// Удалить iframe можно с помощью OneTapButton.destroy();