package com.zylon.enterprise.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class NotificationService {
    
    private static final Logger logger = LoggerFactory.getLogger(NotificationService.class);

    public void sendEmail(String to, String subject, String body) {
        // Mock email sending
        logger.info("Sending email to {}: {}", to, subject);
    }
    
    public void sendInAppNotification(String userId, String message) {
        // Mock in-app notification
        logger.info("Sending in-app notification to {}: {}", userId, message);
    }
}
