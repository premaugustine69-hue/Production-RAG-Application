package com.zylon.enterprise.client;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

@Component
public class FastApiClient {

    private final WebClient webClient;

    public FastApiClient(WebClient.Builder webClientBuilder, @Value("${fastapi.url}") String fastApiUrl) {
        this.webClient = webClientBuilder.baseUrl(fastApiUrl).build();
    }

    public Mono<String> triggerIngestion(String fileId) {
        return webClient.post()
                .uri("/v1/ingest/file")
                .bodyValue("{\"file_id\": \"" + fileId + "\"}")
                .retrieve()
                .bodyToMono(String.class);
    }
}
