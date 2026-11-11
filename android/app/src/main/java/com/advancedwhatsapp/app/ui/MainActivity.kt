package com.advancedwhatsapp.app.ui

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.ai.Psych
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.ChatLock
import com.advancedwhatsapp.app.gateway.GatewayService
import com.advancedwhatsapp.app.notify.Notifier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

// FragmentActivity (hereda de ComponentActivity): BiometricPrompt lo necesita para pedir la huella
class MainActivity : FragmentActivity() {
    private val pendingChat = mutableStateOf<String?>(null)
    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ChatLock.load()
        GatewayService.start(this)
        if (Build.VERSION.SDK_INT >= 33) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        // Al girar el celular se recrea la actividad con el mismo intent: no hay que volver a abrir ese chat
        if (savedInstanceState == null) pendingChat.value = intent.getStringExtra(Notifier.EXTRA_CHAT)
        setContent { AppTheme { AppNav(pendingChat.value) { pendingChat.value = null } } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(Notifier.EXTRA_CHAT)?.let { pendingChat.value = it }
    }

    // Igual que en la PC: "en línea" solo con la app al frente
    override fun onStart() {
        super.onStart()
        AppState.foreground.value = true
        ChatLock.appReturned()   // tras un rato en segundo plano los chats bloqueados vuelven a cerrarse
        lifecycleScope.launch(Dispatchers.IO) { Actions.setPresence(true) }
    }

    override fun onStop() {
        AppState.foreground.value = false
        ChatLock.appLeft()
        lifecycleScope.launch(Dispatchers.IO) { Actions.setPresence(false) }
        super.onStop()
    }
}

@Composable
private fun AppNav(pendingChat: String?, onChatOpened: () -> Unit) {
    val nav = rememberNavController()
    val conn by AppState.connection.collectAsStateWithLifecycle()
    var linkShown by remember { mutableStateOf(false) }

    // Sin vincular: se abre la pantalla de vinculación una sola vez; al conectar, se vuelve a los chats
    LaunchedEffect(conn.status) {
        val needsLink = conn.status == "qr_ready" || conn.status == "logged_out"
        val here = nav.currentDestination?.route
        if (needsLink && !linkShown && here != "link") {
            linkShown = true
            nav.navigate("link")
        } else if (conn.isConnected) {
            linkShown = false
            if (here == "link") nav.popBackStack()
        }
    }
    LaunchedEffect(pendingChat) {
        if (pendingChat != null) {
            nav.navigate("chat/${Uri.encode(pendingChat)}") { launchSingleTop = true }
            onChatOpened()
        }
    }

    NavHost(navController = nav, startDestination = "chats") {
        composable("chats") {
            ChatListScreen(
                onOpen = { nav.navigate("chat/${Uri.encode(it)}") },
                onSettings = { nav.navigate("settings") },
                onStatuses = { nav.navigate("statuses") },
                onLink = { nav.navigate("link") },
                onNewChat = { nav.navigate("new") },
                onSelfAnalysis = { nav.navigate("analysis/${Uri.encode(Psych.SELF_JID)}") },
                onScheduled = { nav.navigate("scheduled") },
            )
        }
        composable("scheduled?chat={chat}", arguments = listOf(navArgument("chat") { type = NavType.StringType; nullable = true })) { e ->
            ScheduledScreen(e.arguments?.getString("chat"), onBack = { nav.popBackStack() })
        }
        composable("link") { LinkScreen(onBack = { nav.popBackStack() }) }
        composable("settings") { SettingsScreen(onBack = { nav.popBackStack() }) }
        composable("statuses") { StatusListScreen(onBack = { nav.popBackStack() }) }
        composable("new") {
            NewChatScreen(onBack = { nav.popBackStack() }, onOpen = { jid ->
                nav.popBackStack()
                nav.navigate("chat/${Uri.encode(jid)}")
            })
        }
        composable("chat/{jid}", arguments = listOf(navArgument("jid") { type = NavType.StringType })) { e ->
            val jid = e.arguments?.getString("jid").orEmpty()
            // Tocar la notificación de otro chat reusa esta pantalla: sin key, el borrador pasaría al otro contacto
            key(jid) {
                // Si el chat está bloqueado, pide contraseña o huella antes de mostrarlo
                LockGate(jid, onBack = { nav.popBackStack() }) {
                    ChatScreen(
                        jid, onBack = { nav.popBackStack() }, onAnalysis = { nav.navigate("analysis/${Uri.encode(jid)}") },
                        onScheduled = { nav.navigate("scheduled?chat=${Uri.encode(jid)}") },
                    )
                }
            }
        }
        composable("analysis/{jid}", arguments = listOf(navArgument("jid") { type = NavType.StringType })) { e ->
            AnalysisScreen(e.arguments?.getString("jid").orEmpty(), onBack = { nav.popBackStack() })
        }
    }
}
