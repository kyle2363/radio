package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import dev.radio.mobile.connection.DiscoveryRepository
import dev.radio.mobile.connection.DiscoveryState
import kotlinx.coroutines.launch

@Composable
internal fun DiscoverySettings(repository:DiscoveryRepository,state:DiscoveryState){
    val scope= rememberCoroutineScope()
    var project by remember(state.projectId){mutableStateOf(state.projectId)}
    var apiKey by remember(state.apiKey){mutableStateOf(state.apiKey)}
    var email by remember(state.email){mutableStateOf(state.email)}
    var password by remember{mutableStateOf("")}
    Column(Modifier.fillMaxWidth().padding(top=12.dp).testTag("discovery-settings")) {
        Text("Automatic server discovery",style=MaterialTheme.typography.titleSmall)
        Text("Uses one Firebase document to find the paired server. The Firebase reader password is used once and never saved.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(project,{project=it},label={Text("Firebase project ID")},singleLine=true,modifier=Modifier.fillMaxWidth().testTag("discovery-project"))
        OutlinedTextField(apiKey,{apiKey=it},label={Text("Firebase Web API key")},singleLine=true,modifier=Modifier.fillMaxWidth().testTag("discovery-api-key"))
        TextButton(onClick={scope.launch { repository.configure(project.trim(),apiKey.trim()) }},enabled=project.isNotBlank()&&apiKey.isNotBlank()){Text("Save Firebase configuration")}
        if(!state.signedIn){
            OutlinedTextField(email,{email=it},label={Text("Reader email")},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Email),modifier=Modifier.fillMaxWidth().testTag("discovery-email"))
            OutlinedTextField(password,{password=it},label={Text("Reader password")},singleLine=true,visualTransformation=PasswordVisualTransformation(),keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Password,autoCorrectEnabled=false),modifier=Modifier.fillMaxWidth().testTag("discovery-password"))
            TextButton(onClick={val secret=password;password="";scope.launch{repository.signIn(email.trim(),secret)}},enabled=email.isNotBlank()&&password.isNotEmpty()&&!state.busy,modifier=Modifier.testTag("discovery-sign-in")){Text("Sign in")}
        }else Row { TextButton(onClick={scope.launch{repository.signOut()}},enabled=!state.busy,modifier=Modifier.testTag("discovery-sign-out")){Text("Sign out of discovery")};TextButton(onClick={scope.launch{repository.readExplicitCheck()}},enabled=!state.busy&&state.signedIn,modifier=Modifier.testTag("discovery-check")){Text("Check discovery")} }
        Text(state.message,Modifier.padding(vertical=6.dp).testTag("discovery-status"),style=MaterialTheme.typography.bodySmall)
        if(state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
    }
}
