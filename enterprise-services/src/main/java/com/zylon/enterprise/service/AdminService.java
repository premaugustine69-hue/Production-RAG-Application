package com.zylon.enterprise.service;

import com.zylon.enterprise.domain.Organization;
import com.zylon.enterprise.domain.User;
import com.zylon.enterprise.repository.OrganizationRepository;
import com.zylon.enterprise.repository.UserRepository;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.UUID;

@Service
public class AdminService {

    private final OrganizationRepository organizationRepository;
    private final UserRepository userRepository;

    public AdminService(OrganizationRepository organizationRepository, UserRepository userRepository) {
        this.organizationRepository = organizationRepository;
        this.userRepository = userRepository;
    }

    public List<Organization> getAllOrganizations() {
        return organizationRepository.findAll();
    }

    public List<User> getUsersByOrganization(UUID orgId) {
        return userRepository.findByOrganizationId(orgId);
    }
    
    public void deactivateUser(UUID userId) {
        userRepository.findById(userId).ifPresent(user -> {
            user.setActive(false);
            userRepository.save(user);
        });
    }
}
